"""Private-network session transport. Requires an encrypted trusted private network."""
import json
import math
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.parse import urlsplit

def server_url(value):
    p=urlsplit(value.strip())
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('Enter a base HTTPS URL, without a path or credentials')
    if p.scheme=='http' and p.hostname not in ('127.0.0.1','localhost'):
        raise ValueError('HTTPS is required except for a local loopback test')
    return value.strip().rstrip('/')+'/sync'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Server redirected the request; use its final HTTPS base URL')

def utc(): return datetime.now(timezone.utc).isoformat()

def triple(v):
    if v is None: return None
    if not isinstance(v,list) or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1e9 for x in v):
        raise ValueError('Invalid coordinate/angle tuple')
    return v

def reading_age(stamp):
    try:
        dt=datetime.fromisoformat(stamp)
        if dt.tzinfo is None:return None
        return max(0.,(datetime.now(timezone.utc)-dt).total_seconds())
    except (ValueError,TypeError):return None

def payload_from_file(path):
    raw=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    orient=raw.get('orientation') or {}
    return dict(position=triple(raw.get('position_km')),angles=triple(orient.get('components')),
                position_stamp=raw.get('last_accepted_at'),orientation_stamp=orient.get('last_accepted_at'),
                position_age=reading_age(raw.get('last_accepted_at')),orientation_age=reading_age(orient.get('last_accepted_at')),
                state=str(raw.get('state','unknown'))[:64],orientation_state=str(orient.get('state','unknown'))[:64])

class Session:
    def __init__(self, directory, token):
        self.token=token
        self.lock=threading.Lock()
        self.players={}
        self.session_id=str(uuid.uuid4())
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        self.path=directory/('session_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.jsonl')
        self.file=self.path.open('x',encoding='utf-8')
        self.error=''
        self.write(dict(event='session_start',schema_version=1,session_id=self.session_id,time=utc(),
                        position_units='km',orientation_order='raw CamDir A B C',
                        time_basis='Host receipt time minus client-reported sample age; network latency not removed'))

    def write(self,event):
        try:
            self.file.write(json.dumps(event,allow_nan=False)+'\n');self.file.flush()
        except OSError as exc:self.error='Recording failed: '+str(exc)

    def sync(self,data):
        pid=str(uuid.UUID(data['id']))
        name=data['name']
        if not isinstance(name,str) or not 1<=len(name)<=32 or not name.isprintable():raise ValueError('Name must be 1–32 printable characters')
        seq=data['seq']
        if type(seq) is not int or seq<0:raise ValueError('Invalid sequence')
        sample=data.get('sample')
        if sample is not None:
            if not isinstance(sample,dict):raise ValueError('Invalid sample')
            sample={k:sample.get(k) for k in ('position','angles','position_stamp','orientation_stamp','position_age','orientation_age','state','orientation_state')}
            triple(sample['position']);triple(sample['angles'])
            for k in ('position_age','orientation_age'):
                v=sample[k]
                if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e9):raise ValueError('Invalid age')
            for k in ('position_stamp','orientation_stamp','state','orientation_state'):
                if sample[k] is not None and (not isinstance(sample[k],str) or len(sample[k])>128):raise ValueError('Invalid timestamp/state')
        now=time.monotonic()
        with self.lock:
            for inactive in [key for key,p in self.players.items() if now-p['seen']>120]:
                self.write(dict(event='player_expired',id=inactive,time=utc()))
                del self.players[inactive]
            old=self.players.get(pid)
            if old is None:
                if len(self.players)>=16:raise ValueError('Session is full (16 identities); restart host for a new session')
                self.write(dict(event='join',id=pid,name=name,time=utc()))
                old=dict(seq=-1,sample=None,received=now,seen=now,name=name,heartbeat=0)
                self.players[pid]=old
            if seq>old['seq']:
                if now-old['seen']>5:self.write(dict(event='reconnected',time=utc(),id=pid))
                old.update(seq=seq,seen=now,name=name)
                if now-old['heartbeat']>=5:
                    self.write(dict(event='heartbeat',time=utc(),id=pid,source_readable=sample is not None))
                    old['heartbeat']=now
                if sample is not None:
                    # Every heartbeat carries age; only new measurements/state need disk records.
                    prev=old['sample']
                    keys=('position_stamp','orientation_stamp','position','angles','state','orientation_state')
                    if prev is None or any(prev.get(k)!=sample.get(k) for k in keys):
                        self.write(dict(event='sample',time=utc(),id=pid,name=name,seq=seq,sample=sample))
                    old.update(sample=sample,received=now)
            players=[]
            for identity,p in self.players.items():
                s=dict(p['sample']) if p['sample'] else None
                if s:
                    for k in ('position_age','orientation_age'):
                        if s[k] is not None:s[k]+=now-p['received']
                players.append(dict(id=identity,name=p['name'],sample=s,seconds_since_contact=now-p['seen']))
            return dict(session_id=self.session_id,server_time=utc(),players=players,recording_error=self.error)

    def close(self):
        with self.lock:
            self.write(dict(event='session_end',time=utc()));self.file.close()

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,session):
        self.session=session
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(4)
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path!='/sync':self.send_error(404);return
        if not secrets.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+self.server.session.token).encode()):self.send_error(401);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16384:raise ValueError('Request too large')
            data=json.loads(self.rfile.read(size))
            result=self.server.session.sync(data)
        except (ValueError,TypeError,KeyError) as exc:self.send_error(400,str(exc));return
        content=json.dumps(result,allow_nan=False).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)

class Client:
    def __init__(self,url,token,name,path):
        self.url=server_url(url)
        self.token,self.name,self.path=token,name,path
        self.id=str(uuid.uuid4());self.seq=0
        self.stop_event=threading.Event();self.lock=threading.Lock()
        self.snapshot=None;self.error='Connecting';self.file_error='';self.received=0
        self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self):self.thread.start()
    def run(self):
        opener=build_opener(ProxyHandler({}),NoRedirect())
        while not self.stop_event.is_set():
            try:
                sample=payload_from_file(self.path);file_error=''
            except (OSError,ValueError,TypeError,AttributeError) as exc:sample=None;file_error='Local OCR: '+str(exc)
            self.seq+=1
            body=json.dumps(dict(id=self.id,name=self.name,seq=self.seq,sample=sample),allow_nan=False).encode()
            try:
                req=Request(self.url,data=body,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
                with opener.open(req,timeout=3) as response:reply=json.loads(response.read(262144))
                with self.lock:self.snapshot=reply;self.received=time.monotonic();self.error='';self.file_error=file_error
            except Exception as exc:
                with self.lock:self.error='Connection: '+str(exc);self.file_error=file_error
            self.stop_event.wait(.25 if not self.error else 1.)
    def view(self):
        with self.lock:return self.snapshot,self.received,self.error,self.file_error
    def close(self):self.stop_event.set()
