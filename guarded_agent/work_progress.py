"""Track unchanged evidence across interleaved tool calls, without replaying effects."""
from hashlib import sha256
import json
import re


def pending_action(text, objective):
    """Detect an explicit promise to act, never interpret prose as a tool call."""
    if re.match(r'\s*(解释|介绍|如何|怎么|能否|你能|explain\b|how\b)', objective, re.I):
        return False
    if re.search(r'无法(?!一次性|一次|同时|全部)|不能继续|缺少|需要你|请提供|等待.{0,8}(?:确认|授权)|未完成原因|blocked|cannot', text, re.I):
        return False
    return bool(re.search(
        r'(?:我(?:将|会|准备|现在|接下来|先|要)|接下来(?:我|将)|正在|下一步[：:])'
        r'[^。！？\n]{0,60}(?:读取|搜索|查找|修改|修复|创建|写入|保存|运行|执行|调用|打开|检查|测试)'
        r'|\bI\s+(?:will|am going to)\s+(?:read|search|edit|write|run|execute|open|check|test)\b', text, re.I))


def request_key(name, arguments):
    return json.dumps([name, arguments], ensure_ascii=False, sort_keys=True)


def result_detail(result):
    detail = result.get('error') or result.get('reason') or result.get('note') or ''
    if result.get('next_read'):
        detail += '\n下一段 read_file 参数：' + json.dumps(result['next_read'], ensure_ascii=False)
    output = result.get('output', '')
    if 'exit_code' in result:
        detail += f"\n命令退出码：{result['exit_code']}"
    if output:
        detail += '\n' + (output if len(output) <= 2000 else output[:800] + '\n[省略中间输出]\n' + output[-1200:])
    return detail.strip()[:3000]


class WorkProgress:
    def __init__(self):
        self.revision = 0
        self.commands = {}
        self.versions = {}
        self.evidence = set()
        self.stale_steps = 0
        self.last_failure = None
        self.repeated_searches = 0

    def repeated_failure(self, name, arguments):
        previous = self.commands.get(request_key(name, arguments))
        if previous and previous[0] == self.revision:
            return previous[1]
        return None

    def observe(self, name, arguments, result):
        # Host-generated tokens and log paths change even when the evidence does not.
        transient = {'edit_version', 'edit_note', 'snapshot_id', 'log', 'backup', 'change_id', 'duration', 'elapsed',
                     'elapsed_seconds', 'timestamp', 'updated'}
        def stable(value):
            if isinstance(value, dict):
                return {k: stable(v) for k, v in value.items() if k not in transient}
            if isinstance(value, list): return [stable(v) for v in value]
            return value
        key = request_key(name, arguments)
        evidence = stable(result)
        if name == 'search_web':
            # Rewording a query or changing snippets is not a new source.
            evidence = {'outcome': result.get('outcome'), 'sources': sorted({
                str(item.get('url') or item.get('link') or item.get('redirect_url') or item.get('title', ''))
                for item in result.get('results', []) if isinstance(item, dict)})}
        signature = sha256(json.dumps([name, evidence], ensure_ascii=False,
                                      sort_keys=True).encode()).hexdigest()
        novel = signature not in self.evidence and name not in {
            'update_plan', 'task_status', 'load_tool_groups', 'discover_projects'}
        self.evidence.add(signature)
        if name == 'search_web':
            self.repeated_searches = 0 if novel else self.repeated_searches + 1
        elif novel and name in {'read_file', 'read_files', 'fetch_url', 'edit_file', 'write_file'}:
            self.repeated_searches = 0
        self.stale_steps = 0 if novel else self.stale_steps + 1
        success = result.get('outcome', 'success') == 'success'
        changed = False
        wrote_changed_file = False
        records = result.get('files', []) if name == 'read_files' else [result]
        for record in records:
            if not isinstance(record, dict): continue
            path = record.get('path') or arguments.get('path')
            version = record.get('sha256')
            if path and version:
                path = str(path).replace('\\', '/').casefold()
                old = self.versions.get(path)
                changed |= old is not None and old != version
                if name in {'write_file', 'edit_file', 'create_document'}:
                    wrote_changed_file |= version != (old or arguments.get('expected_sha256'))
                self.versions[path] = version
        writes = {'move_file',
                  'github_prepare', 'github_publish', 'ui_action', 'desktop_action', 'browser_action'}
        # A different successful command may have repaired the failing test.
        # Re-reading the same files or updating a plan cannot enable a retry.
        if changed or (success and novel and (wrote_changed_file or name in writes | {'run_command', 'github_run_tests'})):
            self.revision += 1
            self.stale_steps = 0
        if result.get('outcome') == 'failed':
            self.last_failure = {'tool': name, 'arguments': arguments, 'result': result}
        if name in {'run_command', 'github_run_tests'}:
            self.commands[key] = (self.revision, result)
