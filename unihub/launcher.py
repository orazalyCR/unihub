"""One-click local launcher. Reuses an existing UniHub server."""
import os, sys, json, urllib.request, webbrowser, threading, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
log=open(ROOT/'launcher.log','a',encoding='utf8',buffering=1)
sys.stdout=log;sys.stderr=log
import server

def running(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:
            data=json.load(r)
            return data.get('app')=='UniHub' and data.get('instance')==hashlib.sha256(server.DB_PATH.encode()).hexdigest()[:16]
    except Exception:return False

if __name__=='__main__':
    preferred=int(os.environ.get('PORT',8000))
    ports=list(dict.fromkeys([preferred,*range(8000,8010)]))
    for port in ports:
        if running(port):
            webbrowser.open(f'http://localhost:{port}')
            sys.exit(0)
    for port in ports:
        try:
            httpd=server.ThreadingHTTPServer(('127.0.0.1',port),server.H)
            break
        except OSError:continue
    else:
        raise RuntimeError('No available local port between 8000 and 8009. See launcher.log.')
    server.init_db()
    threading.Timer(.5,lambda:webbrowser.open(f'http://localhost:{port}')).start()
    httpd.serve_forever()
