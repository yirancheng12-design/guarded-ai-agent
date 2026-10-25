"""Install this checkout into .venv and verify it. Python 3.11+ required."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

PROJECT = 'guarded'
ROOT = Path(__file__).resolve().parents[1]


def environment_python():
    return Path('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')


def commands(profile):
    python = str(environment_python())
    if PROJECT == 'guarded':
        result = [[python, '-m', 'pip', 'install', '-e',
                   '.[windows,assistant]' if profile == 'desktop' else '.']]
        if profile == 'desktop':
            result.append([python, '-m', 'playwright', 'install', 'chromium'])
        return result + [[python, '-m', 'pip', 'check'],
                         [python, '-m', 'guarded_agent', 'doctor'],
                         [python, '-m', 'guarded_agent', 'demo']]
    if PROJECT == 'gpu':
        return [[python, 'tools/verify_repository.py'], [python, 'tools/demo.py']]
    return [[python, 'tools/archive.py', 'verify'], [python, 'tools/archive.py', 'summary']]


def checked(command, root, log, env):
    print('Running: ' + subprocess.list2cmdline(command), flush=True)
    log.write('\n$ ' + subprocess.list2cmdline(command) + '\n')
    log.flush()
    # Preserve the venv interpreter path: POSIX environments often link to base Python.
    executable = str(root / command[0])
    subprocess.run([executable, *command[1:]], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def install(root, profile='core'):
    if sys.version_info < (3, 11):
        raise ValueError('Python 3.11 or newer is required.')
    if profile == 'desktop' and (PROJECT != 'guarded' or os.name != 'nt'):
        raise ValueError('The desktop profile requires Guarded on Windows.')
    root = root.resolve()
    folder = root / '.venv'
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError('Refusing a linked or redirected .venv; choose an ordinary checkout.')
    if folder.exists() and not (folder / 'pyvenv.cfg').is_file():
        raise ValueError('.venv exists but is not a virtual environment. Inspect it before retrying.')
    state = root / '.setup-state.json'
    log_path = root / '.setup.log'
    for target in (state, log_path):
        if target.is_symlink() or target.resolve() != target:
            raise ValueError('Refusing linked installer output: ' + target.name)
    state.write_text(json.dumps({'ok': False, 'profile': profile, 'stage': 'installing'}), encoding='utf-8')
    try:
        with log_path.open('w', encoding='utf-8') as log:
            if not folder.exists():
                print('Creating repository-local .venv ...', flush=True)
                venv.EnvBuilder(with_pip=PROJECT == 'guarded').create(folder)
            python = str(environment_python())
            env = os.environ.copy()
            env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            if PROJECT == 'guarded' and 'PLAYWRIGHT_BROWSERS_PATH' not in env and (root / 'browsers').is_dir():
                env['PLAYWRIGHT_BROWSERS_PATH'] = str(root / 'browsers')
            # Reject a moved/broken environment or a substituted system interpreter.
            probe = ('import pathlib,sys; '
                     'assert sys.version_info >= (3,11), "Python too old"; '
                     'assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(".venv").resolve(), '
                     '"Interpreter does not belong to this checkout"')
            checked([python, '-c', probe], root, log, env)
            for command in commands(profile):
                checked(command, root, log, env)
        state.write_text(json.dumps({'ok': True, 'profile': profile, 'project': PROJECT}), encoding='utf-8')
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        state.write_text(json.dumps({'ok': False, 'profile': profile, 'error': str(exc)}), encoding='utf-8')
        raise
    print('Installation and verification passed. Details: .setup.log', flush=True)
    if PROJECT == 'guarded':
        print('Start: Start-Guarded.cmd (Windows). Chat still requires running Ollama and a tool-capable model.')
    else:
        print('Start: Run.cmd (Windows), or use the documented .venv Python command.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['core', 'desktop'] if PROJECT == 'guarded' else ['core'], default='core')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan as JSON without writes or downloads')
    args = parser.parse_args(argv)
    if args.dry_run:
        print(json.dumps({'project': PROJECT, 'profile': args.profile, 'commands': commands(args.profile)}, indent=2))
        return 0
    try:
        install(ROOT, args.profile)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'Installation failed: {exc}\nInspect .setup.log, fix the cause, then rerun Install.cmd.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
