"""Local chat/task service. Model prose never becomes desktop actions."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
import threading
import time
import urllib.request
import uuid
import base64
import os
from hashlib import sha256
from .engine import Executor
from .journal import Journal
from .ollama_agent import OllamaClient, OllamaAgent, CalculatorSimulation, calculator_profile, parse_profile
from .app_launcher import AppLauncher, open_request


class BusyError(RuntimeError):
    pass


def chat_stream(client, messages, cancelled, on_text):
    body = {"model": client.model, "stream": True, "think": False,
            "messages": [{"role": "system", "content":
                "你是 Guarded 的本地聊天助手。用用户的语言清楚回答。当前处于普通聊天模式，"
                "你不能通过这条普通回复操作桌面，也不能声称已经执行任何动作。界面的助手模式支持明确输入：打开微信、打开WeGame、打开记事本、打开计算器。"
                "需要文件、代码、网页、浏览器或电脑操作时切换到综合助手模式。"}] + messages,
            "options": {"num_ctx": 8192, "num_predict": 2048}, "keep_alive": "5m"}
    request = urllib.request.Request(client.base_url + '/api/chat',
        json.dumps(body, ensure_ascii=False).encode('utf-8'), {'Content-Type': 'application/json'})
    text, done = '', False
    if cancelled():
        return {'outcome': 'cancelled', 'text': ''}
    with client.opener.open(request, timeout=client.timeout) as response:
        while not cancelled():
            line = response.readline(262145)
            if not line:
                break
            if len(line) > 262144:
                raise ValueError('模型返回的数据块过大')
            chunk = json.loads(line)
            if chunk.get('error'):
                raise RuntimeError(chunk['error'])
            message = chunk.get('message', {})
            if message.get('tool_calls'):
                raise ValueError('聊天模式不执行工具调用')
            piece = message.get('content', '')
            if not isinstance(piece, str):
                raise ValueError('模型文本格式无效')
            text += piece
            if len(text) > 100000:
                raise ValueError('模型输出超过长度限制')
            if piece:
                on_text(text)
            if chunk.get('done'):
                done = True
                break
    if cancelled():
        return {'outcome': 'cancelled', 'text': text}
    if not done:
        raise RuntimeError('模型连接中断，请检查 Ollama')
    if not text.strip():
        raise RuntimeError('模型没有返回文本')
    return {'outcome': 'success', 'text': text}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'chat.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,conversation TEXT,role TEXT,
                    content TEXT,mode TEXT,status TEXT,detail TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT,data TEXT);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            ''')
            if 'workspace' not in {r[1] for r in db.execute('PRAGMA table_info(conversations)')}:
                db.execute("ALTER TABLE conversations ADD COLUMN workspace TEXT NOT NULL DEFAULT ''")
            default = db.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
            default = default[0] if default else str(self.directory / 'workspace')
            for row in db.execute("SELECT id FROM conversations WHERE workspace='' ").fetchall():
                workspace = default
                for message in db.execute('SELECT detail FROM messages WHERE conversation=? ORDER BY created DESC', (row['id'],)):
                    try: recorded = json.loads(message['detail']).get('result', {}).get('workspace')
                    except (ValueError, AttributeError): continue
                    if isinstance(recorded, str) and Path(recorded).is_absolute():
                        workspace = recorded; break
                db.execute('UPDATE conversations SET workspace=? WHERE id=?', (str(Path(workspace).resolve()), row['id']))
            db.execute("UPDATE messages SET status='interrupted',content=?,detail='{}' WHERE status='running'",
                ('服务已重启，上次请求未完成。桌面任务结果需要核对；不会自动重复执行。',))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def conversations(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM conversations ORDER BY created DESC')]

    def settings(self, conversation=None):
        with self.connect() as db:
            if conversation:
                row = db.execute('SELECT workspace FROM conversations WHERE id=?', (conversation,)).fetchone()
                if not row: raise ValueError('对话不存在')
            else:
                row = db.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
        workspace = Path(row[0]) if row else self.directory / 'workspace'
        if not row: workspace.mkdir(parents=True, exist_ok=True)
        return {'workspace': str(workspace.resolve())}

    def set_workspace(self, path):
        if not isinstance(path, str) or not Path(path).is_absolute(): raise ValueError('请输入工作文件夹的完整路径')
        folder = Path(path).resolve()
        if folder.parent == folder: raise ValueError('请选择具体工作文件夹，不要选择整个磁盘')
        folder.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('workspace',?)", (str(folder),))
        return self.settings()

    def create(self, workspace=None):
        if workspace is not None:
            workspace = self.set_workspace(workspace)['workspace']
        else:
            workspace = self.settings()['workspace']
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO conversations(id,title,created,workspace) VALUES (?,?,?,?)', (identifier, '新的对话', time.time(), workspace))
        return identifier

    def messages(self, conversation):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM conversations WHERE id=?', (conversation,)).fetchone():
                raise ValueError('对话不存在')
            result = [dict(r) for r in db.execute('SELECT * FROM messages WHERE conversation=? ORDER BY created,rowid', (conversation,))]
        for row in result:
            row['detail'] = json.loads(row['detail'])
        return result

    def begin(self, conversation, content, mode, identifier):
        self.messages(conversation)  # Check existence before writing.
        with self.connect() as db:
            first = not db.execute('SELECT 1 FROM messages WHERE conversation=?', (conversation,)).fetchone()
            if first:
                db.execute('UPDATE conversations SET title=? WHERE id=?', (content[:28], conversation))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex, conversation, 'user', content, mode, 'done', '{}', time.time()))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                (identifier, conversation, 'assistant', '', mode, 'running', '{}', time.time()))

    def finish(self, identifier, content, status, detail):
        with self.connect() as db:
            db.execute('UPDATE messages SET content=?,status=?,detail=? WHERE id=?',
                (content, status, json.dumps(detail, ensure_ascii=False), identifier))

    def profiles(self):
        with self.connect() as db:
            return [{'id': r['id'], 'name': r['name'], 'data': json.loads(r['data'])}
                    for r in db.execute('SELECT * FROM profiles ORDER BY name')]

    def save_profile(self, name, data, identifier=None):
        parse_profile(data)
        identifier = identifier or uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO profiles VALUES (?,?,?)',
                (identifier, name[:80], json.dumps(data, ensure_ascii=False)))
        return identifier


def result_text(result, mode):
    if mode in {'app', 'work'} or result.get('outcome') == 'unsupported':
        return result.get('text', '这个任务不在当前配置的支持范围内，未执行动作。')
    if mode == 'chat':
        return result.get('text') or ('已停止生成。' if result['outcome'] == 'cancelled' else '没有返回文本。')
    status = result['outcome']
    count = len(result.get('steps', []))
    if status == 'success':
        return f'任务已完成，最终目标已经独立验证。\n本次完成 {count} 个步骤。' + ('\n这是计算器模拟演示，没有操作真实桌面。' if mode == 'demo' else '')
    if status == 'cancelled':
        return f'已停止后续动作。已执行步骤的记录已保留（{count} 步）。'
    reason = result.get('reason', '')
    advice = '请查看下方验证记录，核对应用当前状态。'
    if 'session_locked' in reason:
        advice = 'Windows 当前锁屏，请解锁后再开始。'
    elif 'foreground' in reason or 'wrong_input_focus' in reason:
        advice = '目标窗口没有保持前台或输入焦点发生变化。请先核对已执行步骤，再处理焦点。'
    elif 'unresolved_effect' in reason:
        advice = '该窗口有尚未核对的操作结果，后续输入已被阻止。需要独立核对，不能用新对话绕过。'
    return ('结果暂时无法确认，已停止后续动作。' if status == 'unknown' else '任务未完成，已停止。') + '\n' + advice


class Controller:
    def __init__(self, directory, client_factory=OllamaClient, chat_runner=chat_stream, projects_root=None):
        self.store = Store(directory)
        self.client_factory, self.chat_runner = client_factory, chat_runner
        self.lock = threading.RLock()
        self.active = None
        self.cancel_event = None
        self.approval_gate = None
        self.approval_answer = False
        self.browser_service = None
        self.projects_root = Path(projects_root or os.environ.get('GUARDED_PROJECTS_ROOT') or self.store.directory.parent / 'codex项目').resolve()

    def browser(self):
        from .browser_tools import BrowserService
        with self.lock:
            if self.browser_service is None:
                self.browser_service = BrowserService(self.store.directory / 'browser-profile')
            return self.browser_service

    def close(self):
        if self.browser_service:
            self.browser_service.close()
            self.browser_service = None

    def state(self, conversation=None):
        with self.lock:
            result = {'conversations': self.store.conversations(), 'profiles': self.store.profiles(), 'settings': self.store.settings(conversation),
                      'active': json.loads(json.dumps(self.active, ensure_ascii=False)) if self.active else None}
            from .host_policy import read_policy
            policy = read_policy(self.store.directory)
            result['settings'].update({key: policy[key] if policy else False for key in ('auto_execute', 'auto_context', 'full_access')})
            result['messages'] = self.store.messages(conversation) if conversation else []
            return result

    def models(self):
        return [m['name'] for m in self.client_factory('').models() if isinstance(m.get('name'), str)]

    def update_workspace(self, path):
        with self.lock:
            self.idle()
            return self.store.set_workspace(path)

    def create_conversation(self, workspace=None):
        with self.lock:
            if workspace is not None: self.idle()
            return self.store.create(workspace)

    def projects(self):
        from .host_policy import read_policy
        policy = read_policy(self.store.directory)
        if policy: self.projects_root = Path(policy['projects_root']).resolve()
        items = []
        if self.projects_root.is_dir():
            for category in sorted(self.projects_root.iterdir()):
                if not category.is_dir() or not category.resolve().is_relative_to(self.projects_root): continue
                for project in sorted(category.iterdir()):
                    if not project.is_dir() or not project.resolve().is_relative_to(self.projects_root): continue
                    code = []
                    outputs = project / 'outputs'
                    if outputs.is_dir() and outputs.resolve().is_relative_to(project.resolve()):
                        for folder in outputs.iterdir():
                            if folder.is_dir() and folder.resolve().is_relative_to(project.resolve()) and any((folder / marker).is_file() for marker in ('pyproject.toml','package.json','CMakeLists.txt','requirements-desktop.txt')):
                                code.append(folder)
                    root = code[0] if len(code) == 1 else project
                    items.append({'category': category.name, 'name': project.name, 'workspace': str(root.resolve())})
                    if len(items) >= 100: return items
        return items

    def file_tools(self, conversation, run_id=None):
        if not conversation: raise ValueError('请先选择对话')
        return self.work_tools(conversation, run_id or uuid.uuid4().hex, lambda *a: False)

    def work_tools(self, conversation, run_id, approve, **kwargs):
        from .host_policy import read_policy
        policy = read_policy(self.store.directory)
        if policy:
            from .simon_work import SimonWorkTools
            from .runtime_options import apply_options
            tools = SimonWorkTools(self.store.settings(conversation)['workspace'], self.store.directory, run_id, approve,
                conversation_id=conversation, **kwargs, **{key: policy[key] for key in
                    ('auto_execute', 'full_access', 'auto_context', 'allow_web', 'projects_root')})
            apply_options(tools, policy['runtime_options'])
            return tools
        from .work_tools import WorkTools
        return WorkTools(self.store.settings(conversation)['workspace'], self.store.directory,
                         run_id, approve, conversation_id=conversation, browser_factory=self.browser, **kwargs)

    def file_changes(self, conversation, change_id=None):
        from .file_changes import FileChanges
        with self.lock:
            tools = self.file_tools(conversation)
            messages = self.store.messages(conversation)
            changes = FileChanges(tools, {m['id'] for m in messages if m['role']=='assistant'})
            return changes.preview(change_id) if change_id else {'changes': changes.list()}

    def preview_file(self, conversation, path):
        with self.lock:
            tools = self.file_tools(conversation)
            file = tools.path(path)
            if file.suffix.lower() in {'.pdf','.docx','.xlsx','.pptx'}:
                result = tools.read_document(path)
            else:
                result = tools.read_file(path, line_count=300)
                result['truncated'] = result['truncated'] or result['total_lines'] > 300
            return {**result, 'absolute_path': str(file)}

    def restore_change(self, conversation, change_id, expected_sha256):
        from .file_changes import FileChanges
        with self.lock:
            self.idle()
            tools = self.file_tools(conversation)
            owner_runs = {m['id'] for m in self.store.messages(conversation) if m['role']=='assistant'}
            changes = FileChanges(tools, owner_runs)
            record, old = changes.restore_data(change_id, expected_sha256)
            identifier = uuid.uuid4().hex
            self.store.begin(conversation, '恢复文件旧版本：' + record['path'], 'work', identifier)
            try:
                tools = self.file_tools(conversation, identifier)
                written = tools._write_bytes(record['path'], old, expected_sha256)
                result = {'outcome': 'completed' if written['outcome']=='success' else 'unknown',
                    'text': '已恢复旧版本，并保留恢复前的内容备份。' if written['outcome']=='success' else '恢复结果需要核对，请查看文件。',
                    'workspace': str(tools.root), 'artifacts': tools.artifacts,
                    'steps': [{'tool':'restore_file','arguments':{'path':record['path'],'change_id':change_id},'result':written}]}
                try:
                    tools.checkpoint('恢复文件旧版本：' + record['path'], result['steps'], result['outcome'])
                except Exception as exc:
                    result.update(outcome='unknown', text='文件已执行恢复，但任务记录保存失败，请核对文件；不会自动重试。',
                                  reason='restore_checkpoint_unavailable', error=str(exc))
            except Exception as exc:
                result = {'outcome':'error','text':'恢复未完成：'+str(exc),'workspace':str(tools.root)}
            self.store.finish(identifier, result['text'], result['outcome'], {'result': result, 'events': []})
            return result

    def import_file(self, name, data, conversation=None):
        if not isinstance(name, str) or not name or Path(name).name != name or any(c in name for c in '/\\:\x00'):
            raise ValueError('文件名无效')
        allowed = {'.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.txt', '.csv', '.md', '.json', '.py', '.js', '.ts', '.html', '.css'}
        if Path(name).suffix.lower() not in allowed: raise ValueError('请选择普通文档、图片、文本或代码文件')
        if not isinstance(data, str) or len(data) > 28_000_000: raise ValueError('文件不能超过 20 MB')
        try: raw = base64.b64decode(data, validate=True)
        except ValueError: raise ValueError('文件编码无效') from None
        if len(raw) > 20_000_000: raise ValueError('文件不能超过 20 MB')
        from .work_tools import WorkTools
        with self.lock:
            self.idle()
            toolbox = WorkTools(self.store.settings(conversation)['workspace'], self.store.directory, uuid.uuid4().hex, lambda *a: False)
            relative = 'imports/' + uuid.uuid4().hex[:8] + '_' + name
            target = toolbox.path(relative); target.parent.mkdir(parents=True, exist_ok=True)
            if toolbox.path(relative) != target: raise ValueError('文件路径已变化')
            with target.open('xb') as output: output.write(raw)
            return {'path': relative, 'name': name, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}

    def request_approval(self, identifier, title, details, cancel):
        with self.lock:
            if not self.active or self.active['id'] != identifier or cancel.is_set(): return False
            gate = threading.Event()
            self.approval_gate, self.approval_answer = gate, False
            self.active['approval'] = {'id': uuid.uuid4().hex, 'title': title, 'details': details}
            self.active['status'] = 'needs_approval'
        deadline = time.monotonic() + 600
        while not gate.wait(.2):
            if cancel.is_set() or time.monotonic() > deadline: break
        with self.lock:
            allowed = gate.is_set() and self.approval_answer and not cancel.is_set()
            if self.active and self.active['id'] == identifier:
                self.active.pop('approval', None)
                self.active['status'] = 'stopping' if cancel.is_set() else 'running'
            self.approval_gate = None
            return allowed

    def answer_approval(self, identifier, approval_id, allowed):
        with self.lock:
            if type(allowed) is not bool: raise ValueError('确认结果必须为布尔值')
            if not self.active or self.active['id'] != identifier or self.active.get('approval', {}).get('id') != approval_id or not self.approval_gate or self.approval_gate.is_set():
                raise ValueError('这项操作已结束或确认已失效')
            self.approval_answer = allowed
            self.event(identifier, {'kind': 'approval_decision', 'allowed': allowed, 'title': self.active['approval']['title']})
            self.approval_gate.set()

    def import_profile(self, name, data):
        if not isinstance(name, str) or not name.strip() or not isinstance(data, dict):
            raise ValueError('请提供配置名称和 JSON 对象')
        if not isinstance(data.get('actions'), dict) or not all(isinstance(a, dict) for a in data['actions'].values()):
            raise ValueError('actions 必须是动作配置对象')
        if len(data['actions']) > 50:
            raise ValueError('每个任务配置最多 50 个动作')
        return self.store.save_profile(name.strip(), data)

    def idle(self):
        with self.lock:
            if self.active:
                raise BusyError('正在处理一个请求，请等待完成或停止后续动作')

    def windows(self, hwnd=None):
        self.idle()
        from .isolation import ProcessBackend
        with ProcessBackend() as backend:
            return backend.list_windows(timeout=5) if hwnd is None else backend.inspect(int(hwnd), timeout=5)

    def bind(self, identifier, window):
        profiles = {p['id']: p for p in self.store.profiles()}
        if identifier not in profiles:
            raise ValueError('任务配置不存在')
        if not any(w['window'] == window for w in self.windows()):
            raise ValueError('窗口已经变化，请刷新窗口列表')
        profile = profiles[identifier]
        profile['data']['window'] = window
        return self.store.save_profile(profile['name'], profile['data'], identifier)

    def send(self, conversation, text, model, mode='chat', profile_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError('请输入 1 到 12000 个字符')
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError('请选择一个 Ollama 模型')
        if mode not in {'assistant', 'chat', 'demo', 'desktop'}:
            raise ValueError('未知模式')
        app_id = open_request(text) if mode == 'assistant' else None
        if mode == 'assistant':
            mode = 'app' if app_id and app_id != 'unsupported' else 'work'
        if mode == 'demo' and open_request(text):
            raise ValueError('计算器演示不能打开应用。请切换到助手模式，再发送打开微信或打开 WeGame。')
        profile = None
        if mode == 'desktop':
            profile = next((p['data'] for p in self.store.profiles() if p['id'] == profile_id), None)
            if profile is None:
                raise ValueError('执行桌面任务前，请导入并选择任务配置')
            window, _, _ = parse_profile(profile)
            if any(type(v) is not int or v <= 0 for v in asdict(window).values()):
                raise ValueError('任务尚未绑定有效窗口，请先绑定目标窗口')
            if 'REPLACE_' in json.dumps(profile):
                raise ValueError('配置中仍有占位符，请填写真实控件和目标条件')
        with self.lock:
            self.idle()
            identifier = uuid.uuid4().hex
            self.store.begin(conversation, text.strip(), mode, identifier)
            self.active = {'id': identifier, 'conversation': conversation, 'mode': mode,
                'status': 'running', 'text': '', 'events': [], 'started': time.time()}
            self.cancel_event = threading.Event()
            thread = threading.Thread(target=self._run, args=(identifier, conversation, text.strip(), model, mode, profile, self.cancel_event, app_id), daemon=True)
            thread.start()
            return identifier

    def cancel(self, identifier):
        with self.lock:
            if not self.active or self.active['id'] != identifier:
                raise ValueError('请求已结束或不存在')
            self.cancel_event.set()
            self.active['status'] = 'stopping'

    def event(self, identifier, event):
        with self.lock:
            if self.active and self.active['id'] == identifier:
                self.active['events'].append({**event, 'at': time.time()})

    def _run(self, identifier, conversation, text, model, mode, profile, cancel, app_id=None):
        def event(data):
            self.event(identifier, data)
        def update(value):
            with self.lock:
                if self.active and self.active['id'] == identifier:
                    self.active['text'] = value
        try:
            if mode == 'app':
                result = AppLauncher(self.store.directory).run(app_id, identifier, cancel.is_set, event)
                self._finish(identifier, mode, result)
                return
            client = self.client_factory(model)
            if mode == 'work':
                from .work_tools import WorkTools
                from .work_agent import run_work
                previous_messages = self.store.messages(conversation)[:-2]
                history = [{'role': m['role'], 'content': m['content']} for m in previous_messages
                           if m['mode'] in {'chat', 'work'} and m['status'] in {'done', 'success', 'completed'} and m['content']][-8:]
                local_context = any(step.get('tool') in {'list_files', 'read_file', 'search_files', 'read_document', 'describe_image', 'inspect_screen', 'run_command', 'list_windows', 'inspect_window'}
                    for message in previous_messages for step in message.get('detail', {}).get('result', {}).get('steps', []))
                toolbox = self.work_tools(conversation, identifier,
                    lambda title, details: self.request_approval(identifier, title, details, cancel),
                    cancelled=cancel.is_set, event=event, local_context=local_context)
                try: result = run_work(client, text, history, toolbox, cancel.is_set, event, update)
                finally:
                    if hasattr(toolbox, 'close'): toolbox.close()
            elif mode == 'chat':
                history = [{'role': m['role'], 'content': m['content']} for m in self.store.messages(conversation)
                           if m['mode'] == 'chat' and m['status'] in {'done', 'success'} and m['content']][-32:]
                while sum(len(m['content']) for m in history) > 48000 and len(history) > 1:
                    history.pop(0)
                event({'kind': 'chat_started'})
                result = self.chat_runner(client, history, cancel.is_set, update)
            else:
                if mode == 'desktop':
                    event({'kind': 'countdown', 'seconds': 5})
                    if cancel.wait(5):
                        result = {'outcome': 'cancelled', 'reason': 'cancelled_before_activation', 'steps': []}
                        self._finish(identifier, mode, result)
                        return
                # The SAME durable journal is used across every desktop chat.
                journal = Journal(self.store.directory / ('actions.sqlite3' if mode == 'desktop' else 'demo-actions.sqlite3'))
                try:
                    if mode == 'demo':
                        from .simulation import FakeClock
                        clock = FakeClock()
                        backend = CalculatorSimulation(clock)
                        executor = Executor(backend, journal, clock=clock, sleep=clock.sleep)
                        result = OllamaAgent(client, executor, cancelled=cancel.is_set, on_event=event).run(
                            text, *calculator_profile(), identifier)
                    else:
                        from .isolation import ProcessBackend
                        with ProcessBackend(evidence_dir=str(self.store.directory / 'evidence' / identifier)) as backend:
                            executor = Executor(backend, journal)
                            result = OllamaAgent(client, executor, cancelled=cancel.is_set, on_event=event).run(
                                text, *parse_profile(profile), identifier)
                finally:
                    journal.close()
                if not result.get('steps') and result.get('reason', '').startswith('model_stopped:'):
                    result['outcome'] = 'unsupported'
                    result['text'] = '这个任务不在当前配置的支持范围内，未执行动作。打开微信或 WeGame 请使用助手模式。'
            self._finish(identifier, mode, result)
        except Exception as exc:
            detail = f'{type(exc).__name__}: {exc}'
            result = {'outcome': 'error', 'reason': detail}
            advice = '请求未完成。请确认 Ollama 正在运行、模型可用。' if mode == 'chat' else '任务未完成，请查看原因；不会自动重发动作。'
            self._finish(identifier, mode, result, advice + '\n' + detail)

    def _finish(self, identifier, mode, result, content=None):
        with self.lock:
            events = self.active['events'] if self.active and self.active['id'] == identifier else []
            self.store.finish(identifier, content or result_text(result, mode), result['outcome'], {'result': result, 'events': events})
            if self.active and self.active['id'] == identifier:
                self.active = None
                self.cancel_event = None
