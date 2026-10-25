"""Short-lived, token-scoped static preview of the bound workspace."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import secrets
import threading
from urllib.parse import quote, unquote, urlsplit


class WorkspacePreview:
    def __init__(self, tools):
        self.tools = tools
        self.token = secrets.token_urlsafe(24)
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.serve(True)
            def do_HEAD(self):
                self.serve(False)
            def serve(self, body):
                try:
                    path = unquote(urlsplit(self.path).path)
                    prefix = '/' + owner.token + '/'
                    if not path.startswith(prefix): raise ValueError('token')
                    relative = path[len(prefix):]
                    file = owner.tools.path(relative)
                    if not file.is_relative_to(owner.tools.root): raise ValueError('outside workspace')
                    if file.is_dir(): file = owner.tools.path(relative.rstrip('/') + '/index.html')
                    if not file.is_relative_to(owner.tools.root): raise ValueError('outside workspace')
                    if not file.is_file() or file.stat().st_size > 20_000_000: raise ValueError('file')
                    raw = file.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', (mimetypes.guess_type(file.name)[0] or 'application/octet-stream') + '; charset=utf-8')
                    self.send_header('Content-Length', str(len(raw)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'self'")
                    self.end_headers()
                    if body: self.wfile.write(raw)
                except (ValueError, OSError):
                    self.send_error(404)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def url(self, path):
        return self.origin + '/' + self.token + '/' + quote(path.replace('\\', '/'), safe='/')

    def owns(self, url):
        parsed = urlsplit(url)
        return (f'{parsed.scheme}://{parsed.netloc}' == self.origin and
                parsed.path.startswith('/' + self.token + '/'))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
