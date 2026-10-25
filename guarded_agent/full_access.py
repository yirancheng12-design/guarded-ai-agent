"""Explicit, host-owned access to local files under the Windows user's ACLs."""
from pathlib import Path
from types import SimpleNamespace
import re


def local_path(root, value, allow_root=False):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('需要本地文件路径')
    if value.startswith(('\\\\', '//')) or ':' in value[2:]:
        raise ValueError('不支持网络共享、设备路径或备用数据流')
    candidate = (Path(root) / value).resolve()
    if not re.fullmatch(r'[A-Za-z]:\\', candidate.anchor):
        raise ValueError('需要本机磁盘路径')
    from .work_tools import WorkTools
    # Reuse credential and canonical-path checks with this local drive as root.
    drive = Path(candidate.anchor)
    return WorkTools.path(SimpleNamespace(root=drive), str(candidate.relative_to(drive)), allow_root=allow_root)


COMMAND_REVIEW = re.compile(
    r'\b(?:Remove-Item|Clear-Content|del|erase|rmdir|rd|rm|unlink|rmtree|format|diskpart|'
    r'Clear-Disk|Remove-Partition|Remove-Computer|shutdown|Restart-Computer|Stop-Computer|'
    r'Set-MpPreference|Add-MpPreference|Set-NetFirewallProfile|Set-ExecutionPolicy|'
    r'icacls|takeown|reg|bcdedit|runas|Send-MailMessage|Invoke-Expression|iex)\b|'
    r'-Verb\s+RunAs|-EncodedCommand|\b(?:git\s+(?:push|reset|clean)|curl\s+.*(?:-X|-d|--data)|'
    r'Invoke-(?:WebRequest|RestMethod).*-(?:Method|Body)|(?:pip|npm|winget|choco)\s+(?:install|uninstall|remove))\b|'
    r'password|credential|secret|验证码|密码|付款|转账|发布|发送|删除|格式化', re.I)


def command_review_reason(command):
    if COMMAND_REVIEW.search(command):
        return '此命令涉及删除、对外提交、安装、提权、凭据或系统设置，需要核对具体操作。'
    return None
