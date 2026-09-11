"""Host/join a private-network Cellin session. Run python multiplayer.py."""
import math
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace
from collections import deque
import tkinter as tk
from tkinter import filedialog,messagebox
import minimap_base as base
from session_net import Session,Server,Client

class MultiplayerMap(base.Minimap):
    def __init__(self,root,args,client):
        self.client=client;self.remote={}
        super().__init__(root,args)
        root.title('Cellin multiplayer • '+client.name)
        self.net_label=tk.Label(root,text='Connecting…',anchor='w',bg='#152335',fg='#e2eef9',wraplength=950)
        self.net_label.pack(fill='x')
        bar=tk.Frame(root);bar.pack(fill='x')
        tk.Button(bar,text='Show all players',command=self.frame_players).pack(side='left')
        self.network_poll()
    def network_poll(self):
        snapshot,received,error,file_error=self.client.view()
        if snapshot:
            if getattr(self, 'session_id', None)!=snapshot['session_id']:
                self.remote.clear();self.session_id=snapshot['session_id']
            present={p['id'] for p in snapshot['players']}
            for identity in list(self.remote):
                if identity not in present:del self.remote[identity]
            for p in snapshot['players']:
                if p['id']==self.client.id:continue
                state=self.remote.setdefault(p['id'],dict(trail=deque(maxlen=2000),stamp=None,display=None))
                state.update(packet=p,received=received)
                s=p['sample']
                if s and s['position'] is not None:
                    point=tuple(s['position'])
                    if s['position_stamp']!=state['stamp']:
                        if state['display'] is not None and math.dist(state['display'],point)>5:state['trail'].append(None)
                        state['trail'].append(point);state['stamp']=s['position_stamp']
                    state['display']=point
            names=', '.join(p['name']+(' (disconnected)' if p['seconds_since_contact']+time.monotonic()-received>5 else '') for p in snapshot['players'])
            self.net_label.config(text='Players: '+names+' | '+(error or snapshot.get('recording_error') or 'Connected; host recording')+(' | '+file_error if file_error else ''))
        else:self.net_label.config(text=error or 'Connecting…')
        self.root.after(250,self.network_poll)
    def frame_players(self):
        points=[r['display'] for r in self.remote.values() if r['display'] is not None]
        if self.display is not None:points.append(self.display)
        if points:
            self.follow.set(False)
            self.center=tuple((min(p[i] for p in points)+max(p[i] for p in points))/2 for i in range(3))
            self.span=max(.1,max(math.dist(p,self.center) for p in points)*1.3)
    def draw(self):
        super().draw()
        if self.center is None:return
        if self.display is not None:self.canvas.create_text(*self.project(self.display),text=self.client.name+' (you)',anchor='nw',fill='#69ffe3')
        colors=['#ec9aff','#ffda79','#81bfff','#ff9c87']
        for index,state in enumerate(self.remote.values()):
            if state['display'] is None:continue
            p=state['packet'];s=p['sample'];elapsed=time.monotonic()-state['received']
            stale=s['position_age'] is None or s['position_age']+elapsed>3 or s['state']=='stopped'
            disconnected=p['seconds_since_contact']+elapsed>5
            ori_stale=s['orientation_age'] is None or s['orientation_age']+elapsed>5 or s['orientation_state']=='stopped'
            color='#73818d' if stale or disconnected else colors[index%len(colors)]
            previous=None
            for point in state['trail']:
                if point is not None and previous is not None:self.line(previous,point,color)
                previous=point
            pos=state['display'];x,y=self.project(pos)
            if s['angles'] is None:
                self.canvas.create_oval(x-5,y-5,x+5,y+5,outline=color,width=2)
            else:
                verts=[(0,1.6,0),(-1,-1,0),(0,-.5,.55),(1,-1,0),(0,-.5,-.3)]
                pts=[tuple(pos[i]+self.span*.08*v for i,v in enumerate(base.rotate(vertex,s['angles']))) for vertex in verts]
                for a,b in [(0,1),(0,2),(0,3),(0,4),(1,2),(2,3),(3,4),(4,1)]:self.line(pts[a],pts[b],color,2)
                nx,ny=self.project(pts[0]);self.canvas.create_oval(nx-3,ny-3,nx+3,ny+3,fill='white',outline='')
            label=p['name']+(' • disconnected' if disconnected else ' • stale' if stale else '')+(' • orientation stale' if ori_stale else '')
            self.canvas.create_text(x+8,y+8,text=label,anchor='nw',fill=color)

def main():
    root=tk.Tk();root.title('Cellin • Connect to Railway');root.geometry('690x460')
    tk.Label(root,text='Both players connect to the same Railway HTTPS URL and session token.',wraplength=650).pack(pady=12)
    entries={}
    defaults=[('Name','Player'),('Server URL','https://YOUR-SERVICE.up.railway.app'),('Session token',''),('OCR status file',str(Path(__file__).resolve().parent.parent/'tracking_status.json'))]
    for label,value in defaults:
        row=tk.Frame(root);row.pack(fill='x',padx=15,pady=5)
        tk.Label(row,text=label,width=17,anchor='w').pack(side='left')
        field=tk.Entry(row);field.insert(0,value);field.pack(side='left',fill='x',expand=True);entries[label]=field
        if label=='OCR status file':
            def browse(f=field):
                p=filedialog.askopenfilename(filetypes=[('Tracker status','*.json')])
                if p:f.delete(0,'end');f.insert(0,p)
            tk.Button(row,text='Browse',command=browse).pack(side='left')
    tk.Label(root,text='Start your OCR tracker separately. Select your own tracking_status.json.\nUse a unique name. Both players should be in the same game instance.\nNo private network or router port forwarding is required.',justify='left').pack(pady=12)
    def launch():
        try:
            name=entries['Name'].get().strip();url=entries['Server URL'].get().strip();token=entries['Session token'].get().strip();path=entries['OCR status file'].get()
            if not name or len(name)>32 or not name.isprintable() or len(token)<32 or not token.isascii() or any(c.isspace() for c in token):
                raise ValueError('Use a name of 1–32 printable characters and the server token (at least 32 ASCII characters without spaces).')
            client=Client(url,token,name,path)
            for child in root.winfo_children():child.destroy()
            client.start()
            MultiplayerMap(root,SimpleNamespace(status_file=path,demo=False),client)
            def close():
                client.close();root.destroy()
            root.protocol('WM_DELETE_WINDOW',close)
        except (OSError,ValueError) as exc:
            messagebox.showerror('Could not connect',str(exc))
    tk.Button(root,text='Open session',command=launch).pack(pady=8)
    root.mainloop()

if __name__=='__main__':main()
