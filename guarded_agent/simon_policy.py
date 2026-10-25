"""Host-owned review policy. Labels come from the observed UI, never model flags."""
import re

SENSITIVE = re.compile(
    r'发送|提交|发布|删除|清空|清除全部|付款|支付|购买|下单|转账|汇款|充值|订阅|卸载|格式化|'
    r'授权|共享|分享|上传|密码|口令|凭据|密钥|验证码|登录|登陆|注销|退出登录|重置|恢复出厂|'
    r'send|submit|publish|post\b|delete|erase|remove|pay|purchase|buy\b|checkout|transfer|'
    r'upload|share|password|credential|token|secret|sign.?in|log.?in|reset|uninstall|format|'
    r'consent|authorize|subscribe', re.I)
NAVIGATION = re.compile(
    r'(?:搜索|查找|查找下一个|下一项|上一项|返回|后退|前进|上一页|下一页|主页|首页|'
    r'刷新|展开|折叠|放大|缩小|最小化|最大化|帮助|设置|选项|取消|预览|查看|'
    r'search|find|find next|back|forward|next page|previous page|home|refresh|'
    r'expand|collapse|zoom in|zoom out|minimize|maximize|help|settings|options|cancel|preview|view)'
    r'(?:\s*\([^)]*\))?(?:…|\.\.\.)?', re.I)


def review_reason(title, details):
    """None permits routine work. Unrecognized effects retain a concrete review."""
    if title in {'打开应用', '读取目标窗口截图'}:
        return None
    if title != '执行桌面动作':
        return '此操作尚未纳入自动执行范围。'
    action, element = details.get('action', {}), details.get('target_element', {})
    if not element or element.get('password'):
        return '无法确认目标控件，或目标涉及密码。'
    name = str(element.get('name', '')).strip()
    identity = name + ' ' + str(element.get('automation_id', ''))
    if SENSITIVE.search(identity):
        return '目标控件涉及发送、提交、删除、账户或资金等操作。'
    kind, control = action.get('kind'), element.get('control_type')
    if kind == 'type_text' and control in {50004, 50030}:
        # Newlines may submit in a chat or terminal. Shell/credential windows
        # are not ordinary text editors, even if their input is an Edit control.
        context = str(details.get('window', ''))
        if re.search(r'终端|命令提示符|powershell|terminal|cmd\.exe|密码|credential|password', context, re.I):
            return '目标是命令或敏感输入窗口。'
        if any(ord(c) < 32 for c in action.get('text', '')):
            return '输入包含换行或控制字符，可能触发提交。'
        return None
    if kind == 'click' and control in {50004, 50018, 50019, 50023, 50024, 50030}:
        return None  # Focus an editor, scrollbar, tab, or tree; no command button.
    if kind in {'click', 'invoke'} and NAVIGATION.fullmatch(name):
        return None
    if kind == 'toggle' and control == 50002 and re.fullmatch(
            r'自动换行|显示行号|显示状态栏|全屏|深色模式|word wrap|show line numbers|show status bar|dark mode', name, re.I):
        return None
    return '该控件的执行后果尚不明确，需要核对这项操作。'
