"""SimonAI's complete work toolset, with host-owned execution decisions."""
from pathlib import Path
from copy import deepcopy
import json
import re
import shlex
from urllib.parse import urlsplit

from .browser_tools import BrowserService, browser_url
from .simon_bridge import DesktopTools
from .simon_policy import SENSITIVE, NAVIGATION, review_reason
from .work_tools import TOOLS, WorkTools, Declined, console_python, schema, TEXT
from .work_preview import WorkspacePreview


PREVIEW_TOOL = schema('preview_file', 'Open an existing workspace HTML file in the real browser using an owned loopback preview server. Returns real tab/snapshot/element IDs. No shell server needed. Server closes after task; saved files and screenshots remain.', {'path': TEXT}, ['path'])


def automatic_command(command, tools):
    """Single program invocations only. This policy is not an OS sandbox."""
    command = command.strip()
    if command.startswith('& '): command = command[2:].lstrip()
    if not command or any(c in command for c in '\r\n;&|<>`$\x00(){}[],#'):
        return False
    try:
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in '\"\'' else t
                  for t in shlex.split(command, posix=False)]
    except ValueError:
        return False
    if not tokens: return False
    program, args = tokens[0].casefold(), tokens[1:]
    if program == 'mkdir':
        if len(args) != 1 or args[0].startswith('-') or any(c in args[0] for c in '*?'):
            return False
        try: return tools.path(args[0]) != tools.root
        except ValueError: return False
    # Never let an argument escape to another drive or workspace through a path.
    for value in args:
        if value.startswith('-') and '=' in value: value = value.split('=', 1)[1]
        if '\\' in value or '/' in value or '..' in value or ':' in value:
            try: tools.path(value, allow_root=True)
            except ValueError: return False
    if program in {'python', 'python.exe', console_python().casefold()}:
        if args[:2] in (['-m', 'unittest'], ['-m', 'pytest'], ['-m', 'compileall']):
            return not any(arg.startswith(('--override-ini', '-o=')) for arg in args[2:])
        if args and args[0].lower().endswith('.py'):
            try: return tools.path(args[0]).is_file()
            except ValueError: return False
        return False
    if program in {'node', 'node.exe'} and args and args[0].lower().endswith(('.js', '.mjs', '.cjs')):
        try: return tools.path(args[0]).is_file()
        except ValueError: return False
    if program in {'npm', 'npm.cmd'} and (tools.root / 'package.json').is_file():
        return args in (['test'], ['run', 'test'], ['run', 'build'], ['run', 'lint'], ['run', 'typecheck'])
    if program in {'git', 'git.exe'}:
        return args in (['status'], ['status', '--short'], ['diff'], ['diff', '--stat'], ['log', '-5', '--oneline'])
    if program.casefold() == 'write-output' and len(args) == 1:
        return True
    return False


class SimonWorkTools(DesktopTools):
    allowed_names = frozenset(t['function']['name'] for t in TOOLS) | {'verify_app', 'preview_file', 'discover_projects', 'read_files', 'load_tool_groups'}
    model_options = {'num_ctx': 16384, 'num_predict': 4096, 'temperature': .1}

    def __init__(self, *args, allow_web=False, full_access=False, auto_context=False, projects_root=None, **kwargs):
        self.allow_web = allow_web
        self.full_access = full_access
        self.auto_context = auto_context
        self._service = self._preview = None
        self._edit_versions = {}
        super().__init__(*args, browser_factory=self._browser_service, **kwargs)
        self.projects_root = Path(projects_root or self.root.parent / 'codex项目').resolve()
        self.tool_specs = deepcopy([t for t in TOOLS if allow_web or t['function']['name'] not in {'search_web', 'fetch_url'}])
        for tool in self.tool_specs:
            if tool['function']['name'] in {'write_file', 'edit_file'}:
                tool['function']['parameters']['properties']['expected_sha256'] = {'type': 'string', 'description': 'Existing file: copy the short edit_version token from read_file. New file: empty string. Host validates the real SHA256.'}
        self.tool_specs += [DesktopTools.tool_specs[-1], PREVIEW_TOOL,
                           schema('read_files', 'Read up to 8 independent text files in one call. Returns each file with its own edit_version, line count and error. Prefer this when several file paths are already known; never executes or modifies files.', {'paths': {'type': 'array', 'items': TEXT, 'minItems': 1, 'maxItems': 8}}, ['paths']),
                           schema('discover_projects', 'Read local project catalog, current workspace entry documents and prior task state. Use actual local evidence before asking the user for project paths. Read-only; ordinary files only.', {'query': TEXT}, ['query'])]
        from .tool_routing import ToolRouter
        from .planning_prompt import planning_prompt
        self.tool_router = ToolRouter(self.tool_specs)
        self.system_prompt = planning_prompt(self)

    def prepare_task(self, task):
        if not self.auto_context: return []
        arguments = {'query': task[:500]}
        self.event({'kind': 'tool_started', 'tool': 'discover_projects'})
        try: result = self.execute('discover_projects', arguments)
        except (OSError, ValueError) as exc: result = {'outcome': 'failed', 'error': str(exc)}
        self.event({'kind': 'tool_result', 'tool': 'discover_projects', 'outcome': result.get('outcome', 'success')})
        return [{'tool': 'discover_projects', 'arguments': arguments, 'result': result}]

    def path(self, value, allow_root=False):
        if self.full_access:
            from .full_access import local_path
            return local_path(self.root, value, allow_root)
        return super().path(value, allow_root)

    def list_files(self, path='.'):
        if not self.full_access: return super().list_files(path)
        self.local_context = True
        folder = self.path(path, allow_root=True)
        if not folder.is_dir(): raise ValueError('不是文件夹')
        items = []
        for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            try:
                allowed = self.path(str(entry))
                items.append({'name': entry.name, 'directory': allowed.is_dir(),
                              'bytes': allowed.stat().st_size if allowed.is_file() else None})
            except (OSError, ValueError): continue
            if len(items) >= 200: break
        return {'path': str(folder), 'items': items, 'limit': 200}

    def _browser_service(self):
        if self._service is None:
            self._service = BrowserService(self.directory / 'browser-profiles' / self.run_id, headed=False,
                                           allow_external=self.allow_web)
        return self._service

    def execute(self, name, arguments):
        if self.cancelled(): raise Declined('已停止后续操作。')
        if name == 'load_tool_groups':
            if not isinstance(arguments, dict) or set(arguments) != {'groups'}:
                raise ValueError('load_tool_groups 需要 groups')
            return self.tool_router.load(arguments['groups'])
        if name == 'read_files':
            if (not isinstance(arguments, dict) or set(arguments) != {'paths'}
                    or not isinstance(arguments['paths'], list) or not 1 <= len(arguments['paths']) <= 8
                    or any(not isinstance(path, str) or not path.strip() for path in arguments['paths'])):
                raise ValueError('read_files 需要 1 到 8 个文本文件路径')
            results = []
            for path in dict.fromkeys(arguments['paths']):
                try:
                    result = self.execute('read_file', {'path': path})
                except (OSError, ValueError, UnicodeError) as exc:
                    result = {'path': path, 'outcome': 'failed', 'error': str(exc)}
                results.append(result)
            return {'outcome': 'failed' if any(r.get('outcome') == 'failed' for r in results) else 'success', 'files': results}
        if name == 'discover_projects':
            if not isinstance(arguments, dict) or set(arguments) != {'query'} or not isinstance(arguments['query'], str) or len(arguments['query']) > 500:
                raise ValueError('discover_projects 需要不超过 500 字的 query')
            from .local_context import discover
            self.count += 1
            return discover(self, arguments['query'])
        if self.full_access and isinstance(arguments, dict):
            arguments = dict(arguments)
            for key in ('path', 'source', 'destination'):
                if key in arguments and isinstance(arguments[key], str):
                    file = self.path(arguments[key], allow_root=name in {'list_files', 'search_files'})
                    arguments[key] = str(file.relative_to(self.root)) if file.is_relative_to(self.root) else str(file)
        if name in {'write_file', 'edit_file'} and isinstance(arguments, dict):
            version = arguments.get('expected_sha256', '')
            if isinstance(version, str) and version.startswith('read'):
                observed = self._edit_versions.get(version)
                if not observed or self.path(arguments.get('path')) != observed[0]:
                    raise ValueError('读取版本无效或属于其他文件，请重新 read_file 获取 edit_version。')
                arguments = {**arguments, 'expected_sha256': observed[1]}
        if name == 'browser_action' and isinstance(arguments, dict):
            kind, value = arguments.get('kind'), arguments.get('value')
            condition = arguments.get('condition', {})
            if kind in {'fill', 'select'} and condition != {'property': 'value', 'expected': value}:
                raise ValueError('动作尚未发送。fill/select 必须用 condition={property:value, expected:本次输入值} 验证输入本身；重新读取页面后再点击并验证业务结果。')
            if kind == 'check' and condition != {'property': 'checked', 'expected': value == 'true'}:
                raise ValueError('动作尚未发送。check 必须验证 checked 与本次勾选状态一致。')
        if name in {'search_web', 'fetch_url'} and not self.allow_web:
            raise ValueError('公开网页访问已关闭。')
        if name == 'preview_file':
            if not isinstance(arguments, dict) or set(arguments) != {'path'} or not isinstance(arguments['path'], str):
                raise ValueError('preview_file 需要工作区 HTML 相对路径')
            self.count += 1
            file = self.path(arguments['path'])
            if not file.is_relative_to(self.root): raise ValueError('临时预览仅服务工作区；请先将网页复制到工作区。')
            if file.suffix.lower() not in {'.html', '.htm'} or not file.is_file():
                raise ValueError('请先保存 HTML 文件，再预览')
            if self._preview is None: self._preview = WorkspacePreview(self)
            url = self._preview.url(arguments['path'])
            result = self.browser().call('open', url=url)
            return {**result, 'preview_path': arguments['path'], 'note': '临时预览只在本次任务期间开放；HTML 和截图保留在工作区。'}
        result = super().execute(name, arguments)
        if name == 'read_file' and result.get('sha256'):
            version = 'read' + str(len(self._edit_versions) + 1)
            self._edit_versions[version] = (self.path(arguments['path']), result['sha256'])
            result = {**result, 'edit_version': version,
                      'edit_note': '修改此文件时 expected_sha256 使用 ' + version + '；宿主仍会核对文件是否变化。'}
        return result

    def browser_open(self, url):
        browser_url(url)
        owned = self._preview and self._preview.owns(url)
        if not owned and not self.allow_web: raise ValueError('请开启公开网页访问，或用 preview_file 预览工作区 HTML。')
        return super().browser_open(url)

    def browser_action_scope(self, target):
        # A token-bound, static preview has no backend and cannot send network
        # requests. Unknown actions still lock that project, not unrelated ones.
        if self._preview and self._preview.owns(target.get('url', '')):
            return 'static-preview:' + str(self.root).casefold()
        return 'global'

    def review(self, title, details):
        if not self.auto_execute: return super().review(title, details)
        if self.cancelled(): raise Declined('已停止后续操作。')
        reason = None
        if title == '运行命令':
            if self.full_access:
                from .full_access import command_review_reason
                reason = command_review_reason(details['command'])
            elif not automatic_command(details['command'], self):
                reason = '此命令超出常规项目测试/脚本范围，需要核对实际命令。'
        elif title in {'打开浏览器页面', '核对联网内容'}:
            url = details.get('url', '')
            owned = self._preview and self._preview.owns(url)
            parsed = urlsplit(url)
            # Public searches and page reads are routine in host-enabled auto mode,
            # including after local reads and when URLs contain query parameters.
            # Embedded credentials still require a concrete operator review.
            if not owned and (parsed.username is not None or parsed.password is not None):
                reason = '地址包含登录凭据，请核对发送到网站的内容。'
        elif title == '执行网页操作':
            element, url = details.get('element', {}), details.get('url', '')
            parsed, href = urlsplit(url), details.get('element', {}).get('href', '')
            owned = self._preview and self._preview.owns(url)
            if not self.allow_web and (not owned or (href and not self._preview.owns(href))):
                raise ValueError('公开网页访问已关闭，不能离开本次项目预览。')
            if owned and (not href or self._preview.owns(href)):
                reason = None  # Static preview has no backend, forms or external connections.
            elif SENSITIVE.search(element.get('name', '') + ' ' + element.get('type', '')):
                reason = '网页控件涉及提交、账户、发送或其他高影响操作。'
            elif details['kind'] == 'click' and element.get('tag') == 'a' and href and not urlsplit(href).query:
                reason = None
            elif details['kind'] == 'press' and details['value'] in {'Tab', 'Escape', 'ArrowDown', 'ArrowUp'}:
                reason = None
            else:
                reason = '此网页输入可能提交数据，需要核对内容与目标。'
        else:
            reason = review_reason(title, details)
            if self.full_access and title == '执行桌面动作' and reason:
                element, action = details.get('target_element', {}), details.get('action', {})
                identity = str(element.get('name', '')) + ' ' + str(element.get('automation_id', ''))
                if (element and not element.get('password') and not SENSITIVE.search(identity)
                        and action.get('kind') in {'click', 'invoke', 'toggle'}
                        and element.get('control_type') in {50000, 50002, 50003, 50007, 50011, 50013}):
                    reason = None
        self.desktop_prepare_seconds = 5 if reason else 0
        if reason: return WorkTools.review(self, title, {**details, 'review_reason': reason})
        self.event({'kind': 'auto_approved', 'title': title})

    def close(self):
        try:
            if self._service: self._service.close()
        finally:
            if self._preview: self._preview.close()

    def planning_tools(self, task, steps):
        return self.tool_router.select(getattr(self, 'task_objective', task))

    def planning_messages(self, messages, task, steps):
        from .planning_context import build_context
        from .planning_prompt import planning_prompt
        specs = self.planning_tools(task, steps)
        messages = [{'role': 'system', 'content': planning_prompt(self)}] + messages[1:]
        return build_context(messages, task, steps, self.plan,
            getattr(self, 'task_objective', task), self.model_options,
            specs)

    def completion_problem(self, task, steps):
        if re.match(r'\s*(解释|介绍|如何|怎么|能否|你能)', task): return None
        # A prohibition such as “只读检查，不修改项目” is not a write requirement.
        # Keep affirmative clauses after commas so “不要改 A，修改 B” still validates B.
        task = re.sub(r'(?:不要|无需|不必|不用|不需要|禁止|勿|不得|先不|暂不|不)(?:(?:再|先|自动|进行|去|帮我|重复|反复))*(?:创建|修改|更改|修复|写入|保存|实现|生成|运行|执行|测试|打开|访问|删除|读取|搜索|改)[^，,。；;\n]*', '', task)
        successful = {s['tool'] for s in steps if s['result'].get('outcome', 'success') == 'success'}
        if re.search(r'创建|修改|修复|写入|保存|实现|生成', task) and re.search(r'文件|代码|项目|网页|\.py|\.html|\.js', task):
            if not successful & {'write_file', 'edit_file', 'create_document', 'run_command'}:
                return '任务要求保存或修改成果，但没有成功的文件写入或命令执行记录。'
        if re.search(r'运行.{0,25}测试|unittest|pytest|单元测试|npm test', task):
            commands = [s for s in steps if s['tool'] in {'run_command', 'github_run_tests'}
                        and re.search(r'\b(?:unittest|pytest|test\w*)\b', s.get('arguments', {}).get('command', ''), re.I)]
            if (not commands or commands[-1]['result'].get('outcome') != 'success'
                    or commands[-1]['result'].get('exit_code', 0) != 0):
                return '要求运行的测试尚无成功的命令记录。'
            if re.search(r'\bRan 0 tests?\b|\bno tests ran\b|\bcollected 0 items\b', commands[-1]['result'].get('output', '')):
                return '测试命令没有发现任何测试，需要修正测试发现路径并实际运行。'
            last_test = max(i for i, s in enumerate(steps) if s is commands[-1])
            if any(s['tool'] in {'write_file', 'edit_file', 'move_file', 'create_document'}
                   and s['result'].get('outcome', 'success') == 'success' for s in steps[last_test+1:]):
                return '测试通过后文件又发生修改，需要基于最终文件重新运行相关测试。'
        if re.search(r'browser_read|保存.{0,8}截图|浏览器.{0,12}验证', task) and 'browser_read' not in successful:
            return '要求的浏览器检查和截图尚无成功记录。'
        return None
