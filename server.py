"""Railway WSGI entry point. One worker: session state is shared in memory."""
import atexit
import json
import os
from pathlib import Path
import secrets
import threading
import time
from session_net import Session

class Application:
    def __init__(self,token,directory):
        if len(token)<32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('SESSION_TOKEN must contain at least 32 ASCII characters without spaces')
        self.session=Session(directory,token)
        if self.session.error:raise OSError(self.session.error)
        calibration=Path(__file__).with_name('calibration.json')
        if calibration.exists():self.session.write(dict(event='display_calibration',calibration=json.loads(calibration.read_text())))
        self.lock=threading.Lock();self.window=time.monotonic();self.requests=0
    def __call__(self,env,start):
        def reply(code,data):
            body=json.dumps(data).encode()
            start(code,[('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store')])
            return [body]
        if env.get('PATH_INFO')=='/health' and env.get('REQUEST_METHOD')=='GET':
            return reply('200 OK',{'status':'ok'})
        if env.get('PATH_INFO')!='/sync' or env.get('REQUEST_METHOD')!='POST':
            return reply('404 Not Found',{'error':'Not found'})
        with self.lock:
            now=time.monotonic()
            if now-self.window>=1:self.window=now;self.requests=0
            self.requests+=1
            if self.requests>100:return reply('429 Too Many Requests',{'error':'Retry later'})
        supplied=env.get('HTTP_AUTHORIZATION','').encode()
        expected=('Bearer '+self.session.token).encode()
        if not secrets.compare_digest(supplied,expected):return reply('401 Unauthorized',{'error':'Invalid session token'})
        try:
            size=int(env.get('CONTENT_LENGTH','0'))
            if not 0<size<=16384:return reply('413 Payload Too Large',{'error':'Body must be 1–16384 bytes'})
            data=json.loads(env['wsgi.input'].read(size))
            if not isinstance(data,dict):raise ValueError('Expected object')
            result=self.session.sync(data)
        except (ValueError,TypeError,KeyError,AttributeError):
            return reply('400 Bad Request',{'error':'Invalid update'})
        return reply('200 OK',result)
    def close(self):
        if not self.session.file.closed:self.session.close()

def create_app():
    token=os.environ.get('SESSION_TOKEN','')
    directory=Path(os.environ.get('DATA_DIR','/data'))/'recordings'
    app=Application(token,directory)
    atexit.register(app.close)
    return app

if __name__=='__main__':
    from wsgiref.simple_server import make_server
    app=create_app()
    try:
        with make_server('127.0.0.1',int(os.environ.get('PORT','8765')),app) as server:server.serve_forever()
    finally:app.close()
