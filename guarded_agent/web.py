"""Loopback-only chat UI, served without third-party web dependencies."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import json
import os
import secrets
import socket
import webbrowser
from .chat_service import Controller, BusyError
from . import __version__

ASSETS = Path(__file__).with_name('web_assets')


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits a second server to take the same port.
    # Acquire exclusive ownership before any controller or journal is created.
    allow_reuse_address = os.name != 'nt'
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log chat bodies, query strings or tokens.

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, status, body, content_type='application/json; charset=utf-8'):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self, api=False):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.respond(403, {'error': '仅允许本机访问'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + h for h in hosts}:
            self.respond(403, {'error': '来源不被允许'})
            return False
        if api and not secrets.compare_digest(self.headers.get('X-Guarded-Token', ''), self.server.token):
            self.respond(403, {'error': '页面会话已失效，请刷新页面'})
            return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        if not self.authorized(url.path.startswith('/api/')):
            return
        try:
            if url.path == '/health':
                return self.respond(200, {'app': 'guarded-chat', 'version': 1, 'release': __version__})
            if url.path in {'/', '/app.js', '/style.css'}:
                filename, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                                  '/style.css': ('style.css', 'text/css')}[url.path]
                raw = (ASSETS / filename).read_bytes().replace(b'__GUARDED_TOKEN__', self.server.token.encode())
                return self.respond(200, raw, mime + '; charset=utf-8')
            if url.path == '/api/state':
                conversation = parse_qs(url.query).get('conversation', [None])[0]
                return self.respond(200, self.server.controller.state(conversation))
            if url.path == '/api/models':
                return self.respond(200, {'models': self.server.controller.models()})
            if url.path == '/api/projects':
                return self.respond(200, {'projects': self.server.controller.projects()})
            if url.path == '/api/changes':
                conversation = parse_qs(url.query).get('conversation', [None])[0]
                return self.respond(200, self.server.controller.file_changes(conversation))
            self.respond(404, {'error': '地址不存在'})
        except ValueError as exc:
            self.respond(400, {'error': str(exc)})
        except Exception as exc:
            self.respond(503, {'error': str(exc)})

    def do_POST(self):
        if not self.authorized(api=True):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ValueError('需要 JSON 请求')
            length = int(self.headers.get('Content-Length', '0'))
            maximum = 28_100_000 if urlsplit(self.path).path == '/api/import-file' else 262144
            if not 0 < length <= maximum or self.headers.get('Transfer-Encoding'):
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('需要 JSON 对象')
            c = self.server.controller
            path = urlsplit(self.path).path
            if path == '/api/conversations':
                result = {'id': c.create_conversation(data.get('workspace'))}
            elif path == '/api/send':
                result = {'id': c.send(data.get('conversation'), data.get('text'), data.get('model'),
                                      data.get('mode', 'chat'), data.get('profile_id'))}
            elif path == '/api/cancel':
                c.cancel(data.get('id'))
                result = {'ok': True}
            elif path == '/api/profiles':
                result = {'id': c.import_profile(data.get('name'), data.get('data'))}
            elif path == '/api/settings':
                result = c.update_workspace(data.get('workspace'))
            elif path == '/api/import-file':
                result = c.import_file(data.get('name'), data.get('data'), data.get('conversation'))
            elif path == '/api/file-preview':
                result = c.preview_file(data.get('conversation'), data.get('path'))
            elif path == '/api/change-preview':
                result = c.file_changes(data.get('conversation'), data.get('change_id'))
            elif path == '/api/restore-change':
                result = c.restore_change(data.get('conversation'), data.get('change_id'), data.get('expected_sha256'))
            elif path == '/api/approval':
                c.answer_approval(data.get('id'), data.get('approval_id'), data.get('allowed'))
                result = {'ok': True}
            elif path == '/api/open-workspace':
                os.startfile(c.store.settings(data.get('conversation'))['workspace'])
                result = {'ok': True}
            elif path == '/api/windows':
                result = {'windows': c.windows()}
            elif path == '/api/inspect':
                result = c.windows(data['hwnd'])
            elif path == '/api/bind':
                result = {'id': c.bind(data['profile_id'], data['window'])}
            else:
                return self.respond(404, {'error': '地址不存在'})
            self.respond(200, result)
        except BusyError as exc:
            self.respond(409, {'error': str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception as exc:
            self.respond(500, {'error': str(exc)})


def make_server(directory, port=8765, controller=None):
    server = LocalHTTPServer(('127.0.0.1', port), Handler)
    server.token = secrets.token_urlsafe(32)
    try:
        server.controller = controller or Controller(directory)
    except Exception:
        server.server_close()
        raise
    return server


def serve(port=8765, directory=None, open_browser=False):
    directory = directory or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GuardedAgent'
    url = f'http://127.0.0.1:{port}'
    try:
        server = make_server(directory, port)
    except OSError:
        # A second launcher opens the existing app; never restart its journal.
        from .ollama_agent import OllamaClient
        try:
            health = OllamaClient('', url, timeout=2).request('/health')
        except Exception:
            raise RuntimeError(f'端口 {port} 被占用，请使用 --port 指定其他端口') from None
        if health.get('app') != 'guarded-chat':
            raise RuntimeError(f'端口 {port} 上运行的不是 Guarded')
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        webbrowser.open(url)
    print('Guarded chat: ' + url, flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        with server.controller.lock:
            if server.controller.cancel_event:
                server.controller.cancel_event.set()
        server.server_close()
        server.controller.close()
