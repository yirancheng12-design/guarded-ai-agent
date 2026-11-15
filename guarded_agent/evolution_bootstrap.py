"""Restart Guarded after a verified repair, rolling back if startup health fails."""
from pathlib import Path
import json
import subprocess
import sys
import time
import urllib.request


def main():
    directory,port=Path(sys.argv[1]),int(sys.argv[2])
    app=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(app))
    from guarded_agent.self_repair import rollback, write_json
    executable=Path(sys.executable)
    script="from guarded_agent.web import serve; serve(port="+str(port)+",directory="+repr(str(directory))+")"
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def start():
        return subprocess.Popen([str(executable),'-c',script],cwd=app,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    child=start()
    for _ in range(60):
        if child.poll() is not None: break
        try:
            with opener.open(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                if json.load(response).get('app')=='guarded-chat':
                    state=directory/'evolution/state.json'
                    old=json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
                    write_json(state,{**old,'status':'active' if old.get('status')!='rolled_back' else 'rolled_back',
                        'message':'已验证修复已启用，启动检查通过' if old.get('status')!='rolled_back' else '上一版本已恢复，启动检查通过','at':time.time()})
                    return
        except (OSError,ValueError): pass
        time.sleep(.5)
    if child.poll() is None:
        child.terminate(); child.wait(timeout=10)
    rollback(app,directory/'evolution')
    write_json(directory/'evolution/state.json',{'status':'rolled_back','message':'更新后启动检查失败，已自动恢复上一版本','at':time.time()})
    start()


if __name__=='__main__': main()
