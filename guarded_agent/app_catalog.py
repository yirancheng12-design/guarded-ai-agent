"""Discover local executables without launching or guessing commands."""
from pathlib import Path
import hashlib
import json
import os
import subprocess

REGISTRATIONS = Path(__file__).resolve().parent.parent / 'registered-apps.json'


def executable_entry(path, name=None):
    path = Path(os.path.expandvars(str(path).strip().strip('"')))
    if not path.is_absolute() or not path.is_file() or path.suffix.casefold() != '.exe':
        return None
    path = path.resolve()
    if str(path).startswith('\\\\') or ':' in str(path)[2:]:
        return None
    identifier = 'exe:' + hashlib.sha256(str(path).casefold().encode()).hexdigest()[:20]
    return identifier, {'name': name or path.stem, 'path': str(path), 'aliases': (name or path.stem, path.stem)}


def discover_executables():
    result = {}
    if os.name != 'nt':
        return result
    import winreg
    def add(path, name=None):
        entry = executable_entry(path, name)
        if entry:
            result.setdefault(*entry)
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths', 0, winreg.KEY_READ | view) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as item:
                                add(winreg.QueryValueEx(item, '')[0])
                        except OSError:
                            pass
            except OSError:
                pass
    # Resolve shortcuts in one read-only Windows Script Host invocation.
    script = r'''
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$resolver=New-Object -ComObject WScript.Shell
$folders=@([Environment]::GetFolderPath('StartMenu'),[Environment]::GetFolderPath('CommonStartMenu'),[Environment]::GetFolderPath('DesktopDirectory'),[Environment]::GetFolderPath('CommonDesktopDirectory'))
$items=@($folders | Where-Object {$_} | ForEach-Object {Get-ChildItem -LiteralPath $_ -Filter *.lnk -Recurse -File} | ForEach-Object {
 $link=$resolver.CreateShortcut($_.FullName)
 if($link.TargetPath -like '*.exe'){[pscustomobject]@{name=$_.BaseName;path=$link.TargetPath;shortcut=$_.FullName;arguments=$link.Arguments}}
})
Get-AppxPackage | ForEach-Object {
 $package=$_; $manifest=Get-AppxPackageManifest -Package $package.PackageFullName
 foreach($application in $manifest.Package.Applications.Application){
  if($application.Executable -like '*.exe'){
   $items += [pscustomobject]@{name=$package.Name;path=(Join-Path $package.InstallLocation $application.Executable);app_id=($package.PackageFamilyName+'!'+$application.Id)}
  }
 }
}
$items | ConvertTo-Json -Compress
'''
    try:
        completed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, encoding='utf-8-sig', timeout=15, creationflags=0x08000000)
        entries = json.loads(completed.stdout or '[]')
        for item in entries if isinstance(entries, list) else [entries]:
            entry = executable_entry(item['path'], item['name'])
            if entry:
                key, value = entry
                value.update({k: item[k] for k in ('shortcut', 'arguments', 'app_id') if k in item})
                if key in result:
                    value['aliases'] = tuple(dict.fromkeys((*value['aliases'], *result[key]['aliases'])))
                result[key] = value
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyError, TypeError):
        pass
    try:
        for item in json.loads(REGISTRATIONS.read_text(encoding='utf-8')):
            add(item['path'], item['name'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


def register_executable(path, name):
    entry = executable_entry(path, name)
    if not entry or not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('请选择存在的本机 EXE，并提供 1 到 100 字的名称')
    try:
        entries = json.loads(REGISTRATIONS.read_text(encoding='utf-8'))
    except FileNotFoundError:
        entries = []
    key, app = entry
    entries = [item for item in entries if item['path'].casefold() != app['path'].casefold()]
    entries.append({'name': name.strip(), 'path': app['path']})
    temporary = REGISTRATIONS.with_suffix('.tmp')
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(REGISTRATIONS)
    return {'id': key, **app, 'outcome': 'success'}
