"""Start this portable installation with all assistant data under its root."""
from pathlib import Path
import os
import sys
import traceback

def configure_environment(root=None):
    # In a checkout this launcher lives at the repository root, not app/.
    ROOT = Path(root or Path(__file__).resolve().parent).resolve()
    for name in ('data', 'workspace', 'logs', 'cache/tmp'):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ and (ROOT / 'browsers').is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / 'browsers')
    os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'cache/tmp')
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PIP_CACHE_DIR'] = str(ROOT / 'cache/pip')
    return ROOT


def main():
    ROOT = configure_environment()
    os.chdir(ROOT)
    log = (ROOT / 'logs/chat.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log

    try:
        from guarded_agent.web import serve
        serve(directory=ROOT / 'data', open_browser=True)
    except Exception:
        traceback.print_exc()
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '启动失败，请查看：' + str(ROOT / 'logs/chat.log'), 'Guarded', 0x10)
        raise SystemExit(1)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
