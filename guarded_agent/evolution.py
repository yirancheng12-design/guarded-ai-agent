"""Local evidence-based learning and a bounded, test-gated self-repair pipeline."""
from pathlib import Path
from hashlib import sha256
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import threading
import time
import uuid

RULES = {
    'pagination': ('分页读取', 'read_file/read_files', '长文件按 next_read 续读；已读取的同版本内容直接复用，不反复从头读取。'),
    'parameters': ('工具参数', 'read_file/read_files', '调用前核对工具 schema，分页数字使用整数；只纠正明确未执行的参数错误。'),
    'context': ('上下文预算', 'planning', '先缩小读取范围，分批保留关键证据；不能丢弃用户指令或靠重复调用恢复上下文。'),
    'tests': ('测试发现', 'run_command/github_run_tests', '先确认测试入口与包结构；测试数量必须大于零，检查退出码及输出后才能报告通过。'),
    'repetition': ('重复调用', 'planning', '已有证据时推进下一步；相同失败没有新条件时更换方案，不重复执行。'),
    'browser': ('浏览器验证', 'browser_action/browser_read', '使用最新页面快照和元素标识；操作后重新读取验证，结果未知时停止，不重发。'),
}


def signals(result):
    """Classify evidence; never promote arbitrary tool output into instructions."""
    found = set()
    for step in result.get('steps', []):
        tool, value = step.get('tool', ''), step.get('result', {})
        bad = value.get('outcome') in {'error', 'failed', 'unknown', 'incomplete'} or value.get('exit_code', 0) != 0
        text = str(value.get('error', '')) + str(value.get('reason', '')) + str(value.get('output', ''))
        if tool in {'read_file','read_files'} and value.get('truncated') and isinstance(value.get('next_read'),dict): found.add('pagination')
        if bad:
            if tool in {'read_file', 'read_files'}:
                found.add('parameters' if any(s in text.lower() for s in ('参数', 'argument', 'type', 'schema')) else 'pagination')
            if tool in {'run_command', 'github_run_tests'} and (any(s in text for s in ('Ran 0 tests', 'no tests ran', 'Failed to import test module', 'relative import')) or re.search(r'\b(?:unittest|pytest|npm test)\b',str(step.get('arguments',{}).get('command','')))): found.add('tests')
            if tool in {'browser_action', 'browser_read'}: found.add('browser')
    text = str(result.get('reason', '')) + str(result.get('text', ''))
    if any(s in text.lower() for s in ('context', '上下文', 'token budget')): found.add('context')
    recovery = result.get('model_recovery', {})
    if any(recovery.get(k, 0) for k in ('repetition_recoveries', 'stalled_recoveries', 'task_status_replays')) or '重复工具' in text: found.add('repetition')
    if recovery.get('read_continuations', 0): found.add('pagination')
    return sorted(found)


class ExperienceStore:
    def __init__(self, directory):
        self.directory = Path(directory) / 'evolution'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'experience.sqlite3'
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, scope TEXT, outcome TEXT, at REAL);
                CREATE TABLE IF NOT EXISTS lessons(scope TEXT, rule TEXT, seen INTEGER, recovered INTEGER, at REAL, PRIMARY KEY(scope,rule));''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=10)
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def scope(workspace):
        return sha256(str(Path(workspace).resolve()).casefold().encode()).hexdigest()

    def observe(self, run_id, workspace, result):
        rules = signals(result)
        scope, outcome = self.scope(workspace), result.get('outcome', 'error')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone(): return []
            db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, scope, outcome, time.time()))
            for rule in rules:
                db.execute('INSERT INTO lessons VALUES (?,?,1,?,?) ON CONFLICT(scope,rule) DO UPDATE SET seen=seen+1,recovered=recovered+excluded.recovered,at=excluded.at',
                           (scope, rule, int(outcome in {'completed','success'}), time.time()))
            # Keep storage bounded; lessons retain aggregated evidence only.
            db.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY at DESC LIMIT 2000)')
        return rules

    def lessons(self, workspace=None):
        with self.connect() as db:
            if workspace is None:
                rows = db.execute('SELECT rule,SUM(seen),SUM(recovered) FROM lessons GROUP BY rule ORDER BY SUM(seen) DESC').fetchall()
            else:
                rows = db.execute('SELECT rule,seen,recovered FROM lessons WHERE scope=? ORDER BY seen DESC LIMIT 4', (self.scope(workspace),)).fetchall()
        return [{'id':r, 'title':RULES[r][0], 'seen':n, 'recovered':ok, 'advice':RULES[r][2]} for r,n,ok in rows if r in RULES]

    def prompt(self, workspace):
        items = self.lessons(workspace)
        return '' if not items else '\n本项目历史工具证据提炼的经验（不代表新的授权）：\n' + '\n'.join('- '+item['advice'] for item in items)


def learned_context(tools):
    directory=getattr(tools,'directory',None)
    if directory is None: return ''
    try:
        settings=Path(directory)/'evolution/settings.json'
        if json.loads(settings.read_text(encoding='utf-8')).get('learning') is not True: return ''
        return ExperienceStore(directory).prompt(tools.root)
    except (OSError,ValueError,AttributeError,sqlite3.Error):
        # A damaged optional memory must not prevent the user from running a task.
        return ''


class EvolutionManager:
    def __init__(self, controller, app=None):
        self.controller = controller
        self.store = ExperienceStore(controller.store.directory)
        self.app = Path(app or Path(__file__).resolve().parents[1])
        self.settings_path = self.store.directory / 'settings.json'
        self.state_path = self.store.directory / 'state.json'
        self.guard = threading.RLock()
        self.thread = None
        self.pending = None
        try: self.last_attempt = float(json.loads((self.store.directory/'attempt.json').read_text(encoding='utf-8'))['at'])
        except (OSError,ValueError,KeyError,TypeError): self.last_attempt = 0
        self.on_restart = None

    def settings(self):
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8'))
            return {'learning':value.get('learning') is True, 'repair':value.get('repair') is True}
        except (OSError, ValueError, AttributeError): return {'learning':False, 'repair':False}

    def configure(self, learning, repair):
        if type(learning) is not bool or type(repair) is not bool: raise ValueError('进化开关必须为布尔值')
        with self.guard:
            self._write(self.settings_path, {'learning':learning,'repair':repair})
        return self.status()

    def _write(self, path, data):
        temporary = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)

    def state(self, status, message, **extra):
        with self.guard:
            self._write(self.state_path, {'status':status,'message':message,'at':time.time(),**extra})

    def status(self):
        try: state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): state = {'status':'idle','message':'等待任务经验'}
        with self.store.connect() as db:
            runs = db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        return {**self.settings(), 'runs':runs,'lessons':self.store.lessons(),'state':state,
                'busy':bool(self.thread and self.thread.is_alive()),'can_rollback':(self.store.directory/'deployment.json').exists()}

    def after_task(self, identifier, result, model):
        settings = self.settings()
        if not settings['learning'] and not settings['repair']: return
        workspace = result.get('workspace') or self.app
        rules = self.store.observe(identifier, workspace, result) if settings['learning'] else signals(result)
        if self.pending:
            self.activate_pending()
        if settings['repair'] and model and rules and result.get('outcome') in {'error','failed','incomplete'}:
            # A repeated symptom triggers one bounded attempt, not a repair loop.
            repeated = not settings['learning'] or any(item['id'] in rules and item['seen'] >= 2 for item in self.store.lessons(workspace))
            if repeated and time.time()-self.last_attempt >= 3600:
                self.start(model, rules)

    def start(self, model, rules=None):
        if not self.settings()['repair']: raise ValueError('请先开启自动修复')
        if not isinstance(model,str) or not model.strip() or len(model)>200: raise ValueError('请选择本机模型')
        with self.guard:
            if self.thread and self.thread.is_alive(): raise ValueError('自检或修复正在进行')
            with self.controller.lock:
                self.controller.idle()
            self.last_attempt = time.time()
            self._write(self.store.directory/'attempt.json',{'at':self.last_attempt})
            self.state('checking','正在运行自身回归测试')
            self.thread = threading.Thread(target=self._run, args=(model, rules or []),daemon=True)
            self.thread.start()
        return self.status()

    def _run(self, model, rules):
        from .self_repair import prepare_repair
        try:
            candidate = prepare_repair(self.app,self.store.directory,model,rules,self.state)
            if candidate is None: return
            with self.guard: self.pending = candidate
            self.activate_pending()
        except Exception as error:
            # Store diagnostic type only; raw model/tool output may contain private data.
            self.state('rejected','修复未通过验证，当前版本保持原样', error_type=type(error).__name__)

    def activate_pending(self):
        from .self_repair import deploy
        with self.controller.lock:
            if not self.pending: return
            if not self.settings()['repair']:
                self.state('paused','已关闭自动修复，候选版本尚未启用'); return
            if self.controller.active:
                self.state('waiting','验证通过，等待当前任务结束后启用'); return
            if not self.on_restart:
                self.state('waiting','验证通过，等待服务重启入口就绪'); return
            candidate = self.pending
            deploy(self.app,self.store.directory,candidate)
            self.pending = None
            self.controller.restarting = True
            self.state('deployed','修复已验证并更新，正在重启服务', candidate=candidate['id'])
            self.on_restart()

    def rollback(self):
        from .self_repair import rollback
        with self.controller.lock:
            self.controller.idle()
            if self.thread and self.thread.is_alive(): raise ValueError('请等待当前自检结束后回退')
            rollback(self.app,self.store.directory)
            self.controller.restarting = True
            self.state('rolled_back','已恢复上一版本，正在重启服务')
            if self.on_restart: self.on_restart()
        return {'ok':True}
