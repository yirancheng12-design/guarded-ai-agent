"""Show the workbench's original HALOFLUX page in its own native window."""
from pathlib import Path
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(os.environ.get('GUARDED_INSTALL_ROOT') or Path(__file__).resolve().parents[1])
HTML = Path(__file__).with_name('native-renderer') / 'haloflux-background.html'
CHAT_PORT = int(os.environ.get('GUARDED_CHAT_PORT', '8765'))
CONTENT_URL = f'http://127.0.0.1:{CHAT_PORT}/api/native-animation-content'
CHAT_TOKEN = os.environ.get('GUARDED_CHAT_TOKEN', '')
PREVIEW_TOKEN = os.environ.get('GUARDED_PREVIEW_TOKEN') or secrets.token_urlsafe(32)
TITLE = '完整工作台 · 原版 HALOFLUX 本地窗口'
NATIVE_ORIGIN = 'http://127.0.0.1:8767'
LOG = ROOT / 'logs/native-animation.log'
MUTEX_NAME = 'Local\\GuardedNativeWorkbenchAnimationWebView'
ASSET_DIR = HTML.parent
FEED_LOCK = threading.Lock()
FEED_STATUS = {'received': False, 'line_count': 0, 'source': '等待工作台会话内容', 'updated': None,
               'window_pid': int(os.environ.get('GUARDED_ANIMATION_WINDOW_PID', '0')),
               'canvas_ready': False, 'canvas_width': 0, 'canvas_height': 0,
               'canvas_frames': 0, 'canvas_fps': 0, 'canvas_reported': None, 'canvas_error': '',
               'displayed_lines': 0}
FEED_PAYLOAD = {'title': '完整工作台实时演算', 'source': '等待工作台会话内容', 'lines': []}
ACTIVE_SHELL_PAGE = b''

SHELL_PAGE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#08090b}
#animation-frame{position:fixed;inset:0;width:100%;height:100%;border:0;display:block}
</style></head><body>
<iframe id="animation-frame" title="原版 HALOFLUX 动画"></iframe>
<script>
const animationFrame=document.getElementById('animation-frame');
const previewToken=new URLSearchParams(location.search).get('token');
let previousFrames=0, previousReport=performance.now();
async function reportCanvas(){
  let state={ready:false,width:0,height:0,frames:0,fps:0,error:'',displayed_lines:0};
  try{
    const canvas=animationFrame.contentDocument.getElementById('stage');
    const stats=animationFrame.contentWindow.__guardedAnimationStats||{};
    const now=performance.now();
    state.frames=stats.frames||0;state.error=stats.error||'';
    state.displayed_lines=stats.displayed_lines||0;
    state.fps=Math.round((state.frames-previousFrames)*10000/Math.max(1,now-previousReport))/10;
    if(canvas){state.width=canvas.width;state.height=canvas.height;}
    state.ready=state.width>0&&state.height>0&&state.frames>previousFrames&&!state.error;
    previousFrames=state.frames;previousReport=now;
  }catch(error){state.error=String(error.message||error).slice(0,240);}
  await fetch('/api/canvas-status',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(state),keepalive:true}).catch(()=>{});
}
let lastContent='', animationLoaded=false;
animationFrame.addEventListener('load',()=>{
  animationLoaded=true;
  lastContent='';
  pollContent();
  reportCanvas();
});
async function pollContent(){
  try{
    const response=await fetch('/api/animation-content',{
      cache:'no-store',headers:{'X-Guarded-Preview-Token':previewToken}});
    if(!response.ok)return;
    const payload=await response.json(),signature=JSON.stringify(payload);
    if(signature===lastContent)return;
    lastContent=signature;
    if(!animationLoaded)return;
    animationFrame.contentWindow.postMessage({type:'guarded-workbench-content',...payload},location.origin);
  }catch{}
}
pollContent();setInterval(pollContent,750);
setInterval(reportCanvas,1000);
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==window||
     event.data?.type!=='guarded-native-animation-content')return;
  const value=event.data.payload;
  if(!value||typeof value.title!=='string'||typeof value.source!=='string'||
     !Array.isArray(value.lines)||value.lines.length>48)return;
  const payload={title:value.title.slice(0,100),source:value.source.slice(0,100),
    lines:value.lines.filter(line=>typeof line==='string').slice(-48).map(line=>line.slice(0,240))};
  animationFrame.contentWindow.postMessage({type:'guarded-workbench-content',...payload},location.origin);
});
animationFrame.src='/haloflux-background.html?v=responsive-9';
</script></body></html>'''.encode('utf-8')


class AnimationServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class AnimationRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        if path == '/':
            if not secrets.compare_digest(parse_qs(url.query).get('token', [''])[0].encode(), PREVIEW_TOKEN.encode()):
                self.send_error(403)
                return
            body, content_type = ACTIVE_SHELL_PAGE, 'text/html; charset=utf-8'
        elif path == '/_status':
            with FEED_LOCK:
                status = dict(FEED_STATUS)
            status['age_seconds'] = (round(time.time() - status['updated'], 1)
                                     if status['updated'] is not None else None)
            status['canvas_age_seconds'] = (round(time.time() - status['canvas_reported'], 1)
                                            if status['canvas_reported'] is not None else None)
            body, content_type = json.dumps(status).encode('utf-8'), 'application/json; charset=utf-8'
        elif path == '/api/animation-content':
            origin = self.headers.get('Origin')
            if (self.client_address[0] != '127.0.0.1' or self.headers.get('Host') != '127.0.0.1:8767' or
                    (origin and origin != NATIVE_ORIGIN) or
                    not secrets.compare_digest(self.headers.get('X-Guarded-Preview-Token', '').encode(), PREVIEW_TOKEN.encode())):
                self.send_error(403)
                return
            with FEED_LOCK:
                body = json.dumps(FEED_PAYLOAD, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        else:
            requested = (ASSET_DIR / path.lstrip('/')).resolve()
            if not requested.is_relative_to(ASSET_DIR) or not requested.is_file():
                self.send_error(404)
                return
            body = requested.read_bytes()
            content_type = mimetypes.guess_type(requested.name)[0] or 'application/octet-stream'
            if content_type.startswith(('text/', 'application/javascript')):
                content_type += '; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy',
                         "default-src 'self' data:; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data:; "
                         "font-src 'self' data: https://fonts.gstatic.com; "
                         "media-src 'self'; connect-src 'self'; frame-src 'self'; "
                         "frame-ancestors 'self'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if (self.path != '/api/canvas-status' or self.client_address[0] != '127.0.0.1' or
                self.headers.get('Host') != '127.0.0.1:8767' or
                self.headers.get('Origin') != NATIVE_ORIGIN or
                self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65_536 or self.headers.get('Transfer-Encoding'):
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(length))
            if (not isinstance(payload, dict) or not isinstance(payload.get('ready'), bool) or
                    any(not isinstance(payload.get(k), int) or not 0 <= payload[k] <= 10_000
                        for k in ('width', 'height', 'displayed_lines')) or
                    not isinstance(payload.get('frames'), int) or not 0 <= payload['frames'] < 2**53 or
                    not isinstance(payload.get('fps'), (int, float)) or not 0 <= payload['fps'] <= 1000 or
                    not isinstance(payload.get('error'), str)):
                self.send_error(400)
                return
            with FEED_LOCK:
                FEED_STATUS.update(canvas_ready=payload['ready'], canvas_width=payload['width'],
                                   canvas_height=payload['height'], canvas_frames=payload['frames'],
                                   canvas_fps=payload['fps'], canvas_reported=time.time(),
                                   canvas_error=payload['error'][:240], displayed_lines=payload['displayed_lines'])
            self.send_response(204)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)


class AnimationApi:
    @staticmethod
    def log(message):
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open('a', encoding='utf-8') as stream:
                stream.write(str(message) + os.linesep)
        except OSError:
            pass


def serve_http():
    global ACTIVE_SHELL_PAGE
    ACTIVE_SHELL_PAGE = SHELL_PAGE
    server = AnimationServer(('127.0.0.1', 8767), AnimationRequestHandler)
    if CHAT_TOKEN:
        threading.Thread(target=poll_workbench_content, daemon=True,
                         name='workbench-content-poller').start()
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()


def poll_workbench_content():
    global FEED_PAYLOAD
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    previous = None
    last_error = None
    while True:
        try:
            request = urllib.request.Request(CONTENT_URL, headers={'X-Guarded-Token': CHAT_TOKEN})
            with opener.open(request, timeout=1.5) as response:
                payload = json.loads(response.read(65_536).decode('utf-8'))
            if not isinstance(payload, dict) or not isinstance(payload.get('lines'), list):
                raise ValueError('工作台内容格式无效')
            payload = {
                'title': str(payload.get('title', '完整工作台实时演算'))[:100],
                'source': str(payload.get('source', '等待工作台会话内容'))[:100],
                'lines': [line[:240] for line in payload['lines'] if isinstance(line, str)][-48:],
            }
            signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if signature != previous:
                with FEED_LOCK:
                    FEED_PAYLOAD = payload
                    FEED_STATUS.update(received=True, line_count=len(payload['lines']),
                                       source=payload['source'], updated=time.time())
                previous = signature
            last_error = None
        except Exception as error:
            if str(error) != last_error:
                AnimationApi.log('content bridge: ' + str(error))
                last_error = str(error)
        time.sleep(.75)


def start_http_server():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open('a', encoding='utf-8')
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '--http-server'], cwd=str(ROOT),
            env={**os.environ, 'GUARDED_ANIMATION_WINDOW_PID': str(os.getpid()),
                 'GUARDED_PREVIEW_TOKEN': PREVIEW_TOKEN},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    finally:
        log.close()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError('本地动画内容桥接服务启动失败')
        try:
            with opener.open(NATIVE_ORIGIN + '/?token=' + PREVIEW_TOKEN, timeout=.5) as response:
                if response.status == 200:
                    return process
        except OSError:
            threading.Event().wait(.1)
    process.terminate()
    raise RuntimeError('等待本地动画内容桥接服务超时')


def run():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user.FindWindowW.restype = ctypes.c_void_p
        user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        hwnd = user.FindWindowW(None, TITLE)
        if hwnd:
            user.ShowWindow(hwnd, 9)
            user.SetForegroundWindow(hwnd)
        kernel.CloseHandle(mutex)
        return
    server_process = None
    try:
        if not HTML.is_file():
            raise FileNotFoundError('原版工作台动画文件不存在：' + str(HTML))
        import webview
        server_process = start_http_server()

        # No js_api is needed: the renderer communicates with the local server.
        # Exposing an API object containing a Window recursively reflects its
        # GUI properties and can deadlock WebView2's Windows message thread.
        window = webview.create_window(
            TITLE, url=NATIVE_ORIGIN + '/?token=' + PREVIEW_TOKEN, width=1100, height=720,
            min_size=(640, 420), fullscreen=False, frameless=False,
            easy_drag=False, on_top=False, focus=True, background_color='#08090b',
            text_select=False, confirm_close=False,
        )
        window.events.loaded += lambda: AnimationApi.log('HALOFLUX window loaded')
        webview.start(
            gui='edgechromium', private_mode=True,
            storage_path=str(ROOT / 'data/native-animation-webview'), debug=False,
        )
    finally:
        if server_process is not None and server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_process.kill()
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        if '--http-server' in sys.argv:
            serve_http()
        elif '--standalone-preview' in sys.argv:
            run()
        else:
            # Compatibility entry for an already-open, older launcher page.
            # The normal workbench now owns its background and opens no window.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from guarded_agent.harness_runtime import install_workbench_background
            install_workbench_background()
            AnimationApi.log('HALOFLUX background embedded in full workbench; standalone launch skipped')
    except Exception:
        AnimationApi.log(traceback.format_exc())
        try:
            ctypes.windll.user32.MessageBoxW(
                None, '本地原版动画启动失败，请查看 logs/native-animation.log。',
                '完整工作台动画', 0x10,
            )
        except Exception:
            pass
