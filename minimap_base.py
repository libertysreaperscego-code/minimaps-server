"""Star Citizen local 3D minimap. Python 3 + Tkinter; no pip packages required.

Reads v12 tracking_status.json. Raw CamDir components are coupled Euler values.
Uses Q Rz(C) Ry(B) Rx(A), nose +Y. Q is fitted to three distant reference
observations: 3.2 degrees training RMS, 4.8-7.9 degrees held-out error.
Approximate calibration; absolute bank is unverified. No velocity correction.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import os
import tempfile
import tkinter as tk
from tkinter import messagebox

REFERENCE = (11.6231, -419.8465, 198.8041)
CALIBRATION_POSITION = (11.6215, -419.8488, 198.7749)
CALIBRATION_ANGLES = (95., 0., 9.)
STATION_TOP_Z = 200.9345
STATION_HEIGHT_M = (STATION_TOP_Z - REFERENCE[2]) * 1000
STATION_BASE_SCALE = STATION_HEIGHT_M / 195.0
FRAME_ROTATION = (
    (-0.17722959466993118, 0.983796470988559, -0.027095653592235463),
    (-0.9787940915559331, -0.17332119073225846, 0.10918741309486664),
    (0.10272194073482376, 0.045872306608788146, 0.9936518174783665),
)


def station_geometry():
    """Illustrative metres, anchored at the bottom red square (0,0,0)."""
    edges=[]
    def line(a,b,color='#72899d',width=1): edges.append((a,b,color,width))
    def ring(z,r,color,center=(0,0),count=48):
        pts=[(center[0]+r*math.cos(i*2*math.pi/count),center[1]+r*math.sin(i*2*math.pi/count),z) for i in range(count)]
        for i in range(count):line(pts[i],pts[(i+1)%count],color,2)
    def tube(z0,z1,r,color):
        ring(z0,r,color);ring(z1,r,color)
        for i in range(8):
            a=i*math.pi/4;line((r*math.cos(a),r*math.sin(a),z0),(r*math.cos(a),r*math.sin(a),z1),color)
    def box(x,y,z,w,h,d,color):
        pts=[(x+sx*w/2,y+sy*h/2,z+sz*d/2) for sx,sy,sz in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]
        for a,b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:line(pts[a],pts[b],color)
    # Bottom disc and square: exact model anchor, approximate physical location.
    tube(0,4,9,'#dd947c')
    for i in range(12):
        a=i*math.pi/6;line((5*math.cos(a),5*math.sin(a),0),(8*math.cos(a),8*math.sin(a),0),'#ffe8ab',2)
    for a,b in [((-2,-2,0),(2,-2,0)),((2,-2,0),(2,2,0)),((2,2,0),(-2,2,0)),((-2,2,0),(-2,-2,0))]:line(a,b,'#ff5f63',3)
    tube(4,165,5,'#9bafbc')
    for z,r in [(18,9),(35,11),(57,13),(95,8),(133,10),(158,6)]:tube(z,z+4,r,'#93a5b1')
    # Main wheel assembly moved to the upper ring position.
    tube(81,86,55,'#569bdb');ring(83,49,'#325c8b');tube(90,94,36,'#58788f')
    for i in range(12):
        a=i*math.pi/6
        line((10*math.cos(a),10*math.sin(a),91),(54*math.cos(a),54*math.sin(a),83),'#667e90')
        if i%2==0:box(43*math.cos(a),43*math.sin(a),87,7,7,5,'#86acd1')
    tube(38,42,24,'#4d91c9')
    for i in range(4):
        a=i*math.pi/2;line((5*math.cos(a),5*math.sin(a),40),(24*math.cos(a),24*math.sin(a),40),'#8dabbc')
    # Asymmetric equipment gives bank comparison a visible reference.
    box(17,0,116,25,7,35,'#b6a066')
    for z in range(100,134,5):line((5,-4,z),(29,-4,z),'#786b4a')
    box(-13,0,124,14,10,27,'#98adb9')
    line((-5,0,105),(-25,0,105),'#9bafbc');ring(105,8,'#a0b8c9',(-25,0),24)
    for x in (-8,8):line((x,0,155),(0,0,185),'#8dabbc')
    line((0,0,165),(0,0,195),'#d8cf9e',2)
    return edges


def station_point(point, scale, angles):
    # Independent station pose; never derived from the player's current attitude.
    x,y,z=(v*scale*STATION_BASE_SCALE/1000 for v in point)
    a,b,c=(math.radians(v) for v in angles)
    y,z=y*math.cos(a)-z*math.sin(a),y*math.sin(a)+z*math.cos(a)
    x,z=x*math.cos(b)+z*math.sin(b),-x*math.sin(b)+z*math.cos(b)
    x,y=x*math.cos(c)-y*math.sin(c),x*math.sin(c)+y*math.cos(c)
    return tuple(REFERENCE[i]+v for i,v in enumerate((x,y,z)))


class DiagnosticRecorder:
    """Append-only raw observations; independent position/orientation timestamps retained."""
    def __init__(self, directory, source):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ('rotation_diagnostic_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.jsonl')
        self.stream = self.path.open('x', encoding='utf-8')
        self.started = time.monotonic()
        self.stage = 'Initial hold'
        self.count = 0
        try:
            self.emit('session_start', source_status_file=str(source), component_order=['A','B','C'],
                      units='angles unverified, positions km', interval_seconds=.2,
                      purpose='Raw single-control rotation diagnostic; no reference aiming or correction applied')
        except Exception:
            self.stream.close()
            raise

    def emit(self, kind, **fields):
        entry = dict(event=kind, recorded_at=datetime.now(timezone.utc).isoformat(),
                     elapsed_seconds=time.monotonic()-self.started, stage=self.stage, **fields)
        self.stream.write(json.dumps(entry, allow_nan=False)+'\n')
        self.stream.flush()

    def set_stage(self, stage):
        self.stage = stage
        self.emit('stage_start')

    def observe(self, path):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('Status is not an object')
            orient = data.get('orientation') or {}
            if not isinstance(orient, dict):
                raise ValueError('Orientation is not an object')
            pa, oa = age(data.get('last_accepted_at')), age(orient.get('last_accepted_at'))
            def finite_age(a):
                return a if math.isfinite(a) else None
            # Keep repeated reads: these show the actual age and cadence of OCR updates.
            payload = dict(raw_status=data, position_age_seconds=finite_age(pa),
                           orientation_age_seconds=finite_age(oa),
                           position_stale=pa>3 or data.get('state')=='stopped',
                           orientation_stale=oa>3 or orient.get('state')=='stopped')
            json.dumps(payload, allow_nan=False)
        except (OSError, ValueError, TypeError) as exc:
            self.emit('read_error', message=str(exc))
            return False
        self.emit('observation', **payload)
        self.count += 1
        return True

    def close(self, reason='Stopped by user'):
        if self.stream.closed:
            return
        try:
            self.emit('session_end', reason=reason, observations=self.count)
        finally:
            self.stream.close()


def reference_sample(data):
    """Capture raw accepted values, never interpolated display values."""
    if not isinstance(data, dict):
        raise ValueError('Invalid tracker status')
    orientation = data.get('orientation')
    if not isinstance(orientation, dict):
        raise ValueError('Orientation is unavailable. Run the tracker with --orientation.')
    if data.get('state') in ('stopped', 'write_error') or orientation.get('state') == 'stopped':
        raise ValueError('Start the OCR tracker before recording.')
    ps, os_ = data.get('last_accepted_at'), orientation.get('last_accepted_at')
    if age(ps) > 3 or age(os_) > 3:
        raise ValueError('Position and orientation must both be less than 3 seconds old. Hold your aim and try again.')
    skew = abs((datetime.fromisoformat(ps) - datetime.fromisoformat(os_)).total_seconds())
    if skew > 2:
        raise ValueError('Position and orientation timestamps are over 2 seconds apart. Hold your aim and try again.')
    position = vector(data.get('position_km'))
    angles = vector(orientation.get('components'))
    delta = tuple(b-a for a,b in zip(position, REFERENCE))
    distance = math.sqrt(sum(x*x for x in delta))
    if distance < .01:
        raise ValueError('Move at least 10 metres from the reference before recording.')
    return dict(recorded_at=datetime.now(timezone.utc).isoformat(),
                reference_km=REFERENCE, position_km=position,
                camdir_raw=angles, component_order=['A','B','C'],
                angle_units='degrees_assumed', position_accepted_at=ps,
                orientation_accepted_at=os_, timestamp_skew_seconds=skew,
                position_age_seconds=age(ps), orientation_age_seconds=age(os_),
                direction_to_reference=unit(delta), distance_m=distance*1000,
                tracker_frame=data.get('frame'),
                aim_basis='User clicked Record reference while aiming fixed ship nose at reference',
                roll_reference=None)


def append_reference_sample(path, sample):
    """Preserve existing data, reject duplicates, replace the file atomically."""
    if path.exists():
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('schema_version') != 1 or not isinstance(payload.get('samples'), list):
            raise ValueError('Existing sample file is invalid; it has not been overwritten.')
    else:
        payload = {'schema_version': 1, 'purpose': 'Reference aiming observations; not a solved rotation calibration', 'samples': []}
    for previous in payload['samples']:
        if not isinstance(previous, dict):
            raise ValueError('Existing sample file contains an invalid sample.')
        if all(previous.get(k) == sample[k] for k in ('position_accepted_at','orientation_accepted_at')):
            raise ValueError('These readings were already recorded. Wait for new readings or move to the next viewpoint.')
    payload['samples'].append(sample)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(payload, stream, indent=2, allow_nan=False)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return len(payload['samples'])


def unit(v):
    length = math.sqrt(sum(x*x for x in v))
    if length < 1e-9:
        raise ValueError('Direction is too small')
    return tuple(x / length for x in v)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def alignment(source, destination):
    """Shortest rigid rotation from source to destination, as a unit quaternion."""
    a, b = unit(source), unit(destination)
    dot = max(-1., min(1., sum(x*y for x, y in zip(a, b))))
    if dot < -0.999999:
        basis = min(((1,0,0), (0,1,0), (0,0,1)), key=lambda v: abs(sum(x*y for x,y in zip(a,v))))
        return (0., *unit(cross(a, basis)))
    q = (1 + dot, *cross(a, b))
    norm = math.sqrt(sum(x*x for x in q))
    return tuple(x / norm for x in q)


def apply_alignment(v, q):
    t = tuple(2*x for x in cross(q[1:], v))
    u = cross(q[1:], t)
    return tuple(v[i] + q[0]*t[i] + u[i] for i in range(3))


def reference_alignment():
    # Assumes the actual ship nose was aimed at REFERENCE for this measurement.
    # One direction constrains the nose, not bank or the full Euler convention.
    direction = tuple(b-a for a,b in zip(CALIBRATION_POSITION, REFERENCE))
    return alignment(rotate((0,1,0), CALIBRATION_ANGLES), direction)


def read_calibration(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    q, signs = data['rotation'], data['inverted']
    if (len(q) != 4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in q)
            or len(signs) != 3 or any(type(x) is not bool for x in signs)):
        raise ValueError('Invalid calibration')
    norm = math.sqrt(sum(x*x for x in q))
    if norm < 1e-9:
        raise ValueError('Invalid rotation')
    return tuple(x/norm for x in q), signs


def write_calibration(path, rotation, inverted):
    payload = json.dumps({'version': 1, 'rotation': rotation, 'inverted': inverted}, indent=2)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(payload)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('Expected three numeric components')
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) for x in value):
        raise ValueError('Invalid component')
    result = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in result):
        raise ValueError('Non-finite component')
    return result


def age(timestamp):
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            return math.inf
        return max(0., (datetime.now(timezone.utc) - dt).total_seconds())
    except (TypeError, ValueError):
        return math.inf


def angle_step(current, target, fraction):
    return current + ((target - current + 180) % 360 - 180) * fraction


def rotate(point, angles):
    """Map the complete local model into Cellin using Q Rz(C) Ry(B) Rx(A)."""
    a, b, c = (math.radians(v) for v in angles)
    x, y, z = point
    y, z = y*math.cos(a)-z*math.sin(a), y*math.sin(a)+z*math.cos(a)
    x, z = x*math.cos(b)+z*math.sin(b), -x*math.sin(b)+z*math.cos(b)
    v = (x*math.cos(c)-y*math.sin(c), x*math.sin(c)+y*math.cos(c), z)
    return tuple(sum(row[i]*v[i] for i in range(3)) for row in FRAME_ROTATION)


def direction_error(a, b):
    a,b = unit(a),unit(b)
    return math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(a,b))))))


def enrich_sample(sample, mode, second_reference=None, phase=None):
    sample['observation_type'] = mode
    sample['rotation_model'] = 'v8_three_distant_views_Q_Rz(C)_Ry(B)_Rx(A)_nose_plusY'
    sample['display_frame_rotation'] = FRAME_ROTATION
    sample['calibration_status'] = 'approximate_nose_absolute_bank_unverified'
    sample['candidate_nose_error_degrees'] = direction_error(rotate((0,1,0), sample['camdir_raw']), sample['direction_to_reference']) if mode != 'roll_sequence' else None
    sample['nose_aim_confirmed'] = mode != 'roll_sequence'
    if mode == 'roll_sequence':
        sample['aim_basis'] = 'User held pitch/yaw controls untouched during roll; reference aiming not asserted'
        sample['roll_phase'] = phase
    if mode == 'nose_and_up':
        second = vector(second_reference)
        relative = tuple(b-a for a,b in zip(sample['position_km'],second))
        forward = sample['direction_to_reference']
        projection = sum(a*b for a,b in zip(relative,forward))
        if projection <= 0:
            raise ValueError('The second landmark must be ahead of the ship and visible above the nose.')
        up = tuple(a-projection*b for a,b in zip(relative,forward))
        if math.sqrt(sum(x*x for x in up)) < .005 or math.sqrt(sum(x*x for x in up))/math.sqrt(sum(x*x for x in relative)) < .1:
            raise ValueError('Choose a second landmark farther from the nose line (at least about 6 degrees).')
        sample['roll_reference'] = dict(second_reference_km=second, alignment='above_fixed_nose_centered_camera', world_up=unit(up))
        sample['candidate_up_error_degrees'] = direction_error(rotate((0,0,1),sample['camdir_raw']),up)
    return sample


class Minimap:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.path = Path(args.status_file).resolve()
        self.target = self.display = self.center = None
        self.center = REFERENCE
        self.angles = self.display_angles = None
        self.last_stamp = self.orientation_stamp = None
        self.status = {}
        self.error = 'Waiting for tracker'
        self.trail = deque(maxlen=2000)
        self.flight_samples = deque(maxlen=12)
        self.correction = (1.,0.,0.,0.)
        self.calibration_note = 'Approximate 3-view calibration • bank unverified'
        self.span = .1
        self.span = .16*STATION_BASE_SCALE
        self.station_scale = 1.
        self.station_angles = (0.,0.,0.)
        self.station_edges = station_geometry()
        self.station_visible = tk.BooleanVar(value=True)
        self.azimuth, self.elevation = -35., 35.
        self.drag = None
        self.last_tick = time.monotonic()
        self.started = self.last_tick
        root.title('Cellin • 3D minimap' + (' • DEMO' if args.demo else ''))
        root.geometry('1000x740')
        root.minsize(680, 480)
        root.configure(bg='#0b1420')
        bar = tk.Frame(root, bg='#152335')
        bar.pack(fill='x')
        self.follow = tk.BooleanVar(value=False)
        self.topmost = tk.BooleanVar(value=False)
        self.signs = [tk.BooleanVar(value=False) for _ in range(3)]
        for title, action in [('Center reference', self.center_reference), ('Center ship', self.recenter), ('Reset view', self.reset_view), ('Clear trail', self.trail.clear)]:
            tk.Button(bar, text=title, command=action).pack(side='left', padx=4, pady=7)
        for title, var, action in [('Follow ship', self.follow, None), ('Always on top', self.topmost, lambda: root.attributes('-topmost', self.topmost.get()))]:
            tk.Checkbutton(bar, text=title, variable=var, command=action).pack(side='left', padx=4)
        self.info = tk.Label(root, bg='#0b1420', fg='#d9eafa', anchor='w', justify='left', font=('Consolas', 11), padx=12, pady=8)
        self.info.pack(fill='x')
        self.canvas = tk.Canvas(root, bg='#080f19', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        bottom = tk.Frame(root, bg='#152335')
        bottom.pack(fill='x')
        calibration = tk.Frame(root, bg='#152335')
        calibration.pack(fill='x')
        station_bar = tk.Frame(root, bg='#152335')
        station_bar.pack(fill='x')
        tk.Checkbutton(station_bar,text='Show station',variable=self.station_visible).pack(side='left',padx=8)
        tk.Button(station_bar,text='Station placement',command=self.station_settings).pack(side='left',padx=4)
        tk.Button(station_bar,text='Frame station',command=self.frame_station).pack(side='left',padx=4)
        tk.Label(station_bar,text='Height 2.1304 km along +Z • widths estimated',bg='#152335',fg='#b5ccdf').pack(side='left',padx=10)
        tk.Button(calibration, text='Record reference / roll', command=self.open_recorder).pack(side='left', padx=8, pady=5)
        tk.Button(calibration, text='Rotation diagnostic', command=self.open_diagnostic).pack(side='left', padx=4)
        self.record_note = tk.Label(calibration, text='Aim fixed nose at reference; hold steady, then record.', bg='#152335', fg='#b5ccdf', wraplength=470, justify='left')
        self.record_note.pack(side='left', padx=8)
        tk.Label(bottom, text='Raw CamDir A / B / C • coupled angles', bg='#152335', fg='white').pack(side='left', padx=8)
        tk.Label(bottom, text='Drag: orbit   Wheel: zoom   +Z up • degrees assumed', bg='#152335', fg='#b5ccdf').pack(side='left', padx=12)
        self.canvas.bind('<ButtonPress-1>', lambda e: setattr(self, 'drag', (e.x, e.y)))
        self.canvas.bind('<B1-Motion>', self.orbit)
        self.canvas.bind('<MouseWheel>', self.zoom)
        self.canvas.bind('<Button-4>', lambda e: self.set_zoom(.85))
        self.canvas.bind('<Button-5>', lambda e: self.set_zoom(1.18))
        self.poll()
        self.tick()

    def frame_station(self):
        self.follow.set(False)
        self.center=station_point((0,0,97.5),self.station_scale,self.station_angles)
        self.span=.16*STATION_BASE_SCALE*self.station_scale

    def station_settings(self):
        window=tk.Toplevel(self.root)
        window.title('Station placement — approximate')
        tk.Label(window,text='Red square anchored at the saved Cellin reference.\nDefault height: 2130.4 m; top Z: 200.9345 km; spine along +Z.\nWidths scale proportionally and remain estimates.\nSize multiplier 1 and zero tilts preserve the supplied height.\nAdjust the station only; player calibration is unaffected.',justify='left').pack(padx=15,pady=12)
        fields=[]
        for label,value in zip(('Size multiplier','Tilt around Cellin X (degrees)','Tilt around Cellin Y (degrees)','Turn around Cellin Z (degrees)'),(self.station_scale,*self.station_angles)):
            row=tk.Frame(window);row.pack(fill='x',padx=15,pady=3)
            tk.Label(row,text=label,width=32,anchor='w').pack(side='left')
            entry=tk.Entry(row,width=12);entry.insert(0,str(value));entry.pack(side='left');fields.append(entry)
        note=tk.Label(window,text='Placement adjustments apply for this session.',fg='#805020');note.pack(pady=8)
        def apply():
            try:
                values=[float(f.get()) for f in fields]
                if not all(math.isfinite(v) for v in values) or not .05<=values[0]<=20:raise ValueError()
                self.station_scale=values[0];self.station_angles=tuple(v%360 for v in values[1:]);self.frame_station()
                note.config(text='Applied. Red reference square remains fixed.')
            except ValueError:note.config(text='Enter finite numbers; size multiplier must be 0.05–20.')
        tk.Button(window,text='Apply and frame station',command=apply).pack(pady=10)

    def draw_station(self):
        if not self.station_visible.get():return
        for a,b,color,width in self.station_edges:
            self.line(station_point(a,self.station_scale,self.station_angles),station_point(b,self.station_scale,self.station_angles),color,width)
        self.canvas.create_text(*self.project(station_point((0,0,195),self.station_scale,self.station_angles)),text='Station top',fill='#b6cadd',anchor='sw')

    def open_diagnostic(self):
        if getattr(self, 'diagnostic_window', None) is not None and self.diagnostic_window.winfo_exists():
            self.diagnostic_window.lift()
            return
        window = self.diagnostic_window = tk.Toplevel(self.root)
        window.title('Pitch / yaw / roll diagnostic')
        window.geometry('740x590')
        stages = ['Initial hold', 'Pitch only', 'Pitch return', 'Hold after pitch',
                  'Yaw only', 'Yaw return', 'Hold after yaw',
                  'Roll only', 'Roll return', 'Final hold']
        tk.Label(window,text='Record separate control movements — no landmark aiming required',font=('Arial',12,'bold')).pack(pady=12)
        tk.Label(window,text='Stop deliberate translation and center freelook/headtracking. Keep the same camera view.\nHold for 3–5 seconds at each hold stage. For each movement, use ONLY the named control\nslowly for about 10 seconds (roughly 30–60 degrees), then return using that same control.\nSmall coordinate changes during rotation are expected and will be preserved.\nThe stage labels describe your actions; they are not automatically detected.',justify='left',wraplength=700).pack(padx=12,pady=8)
        stage = tk.StringVar(value=stages[0])
        tk.Label(window,text='Select the next stage before operating the ship:').pack(pady=6)
        selector = tk.OptionMenu(window,stage,*stages)
        selector.pack()
        note = tk.Label(window,text='Start recording, then follow the stages in order.',wraplength=700,justify='left')
        note.pack(padx=12,pady=15)
        active = {'recorder': None, 'timer': None}

        def stop(reason='Stopped by user'):
            if active['timer'] is not None:
                window.after_cancel(active['timer'])
                active['timer'] = None
            recorder = active['recorder']
            active['recorder'] = None
            if recorder:
                try:
                    recorder.close(reason)
                    note.config(text=f'Saved {recorder.count} observations:\n{recorder.path}\nSend this JSONL file for analysis.')
                except (OSError,ValueError) as exc:
                    note.config(text=f'Could not finish the log: {exc}\nEarlier flushed records remain at {recorder.path}')
            start_button.config(state='normal')
            next_button.config(state='disabled')

        def poll():
            active['timer'] = None
            recorder = active['recorder']
            if recorder is None:
                return
            try:
                ok = recorder.observe(self.path)
                note.config(text=f'Recording: {recorder.stage} • {recorder.count} observations\n' + ('Raw readings saved; stale flags are included.' if ok else 'Status unreadable; error recorded. Check the OCR tracker.'))
            except (OSError,ValueError) as exc:
                stop('Write failure')
                note.config(text=f'Recording stopped because the log could not be written: {exc}')
                return
            active['timer'] = window.after(200,poll)

        def stage_changed(*_):
            recorder=active['recorder']
            if recorder:
                try:
                    recorder.set_stage(stage.get())
                except (OSError,ValueError) as exc:
                    stop('Stage write failure')
                    note.config(text=f'Recording stopped: {exc}')

        def start():
            if self.args.demo:
                note.config(text='Diagnostic recording is disabled in demo mode. Run with the live OCR tracker.')
                return
            try:
                active['recorder']=DiagnosticRecorder(Path(__file__).with_name('rotation_diagnostics'),self.path)
                stage.set(stages[0])
                start_button.config(state='disabled')
                next_button.config(state='normal')
                poll()
            except (OSError,ValueError) as exc:
                stop('Start failure')
                note.config(text=f'Could not start recording: {exc}')

        def advance():
            index=stages.index(stage.get())
            if index < len(stages)-1:
                stage.set(stages[index+1])
            else:
                stop()

        stage.trace_add('write',stage_changed)
        start_button=tk.Button(window,text='Start recording',command=start)
        start_button.pack(pady=4)
        next_button=tk.Button(window,text='Next stage / finish',command=advance,state='disabled')
        next_button.pack(pady=4)
        tk.Button(window,text='Stop and save',command=stop).pack(pady=4)
        tk.Label(window,text='Every observation is flushed to disk. The file contains raw tracker values,\nindependent acceptance timestamps, repeated readings, stale flags, and stage markers.\nThis mode collects evidence; it does not change the minimap alignment.',justify='left').pack(pady=15)

        def close_window():
            stop('Diagnostic window closed')
            window.destroy()
        window.protocol('WM_DELETE_WINDOW',close_window)
        # Parent shutdown may bypass the child close protocol; flush a final marker.
        def destroyed(event):
            if event.widget is window and active['recorder']:
                try:
                    active['recorder'].close('Window destroyed')
                except OSError:
                    pass
                active['recorder']=None
        window.bind('<Destroy>',destroyed)

    def open_recorder(self):
        if getattr(self, 'recorder', None) is not None and self.recorder.winfo_exists():
            self.recorder.lift()
            return
        window = self.recorder = tk.Toplevel(self.root)
        window.title('Reference calibration recorder')
        window.geometry('700x650')
        mode = tk.StringVar(value='nose_reference')
        tk.Label(window, text='Collect observations — recording does not automatically solve calibration.', wraplength=650).pack(pady=8)
        for title,value in [('Nose aimed at the Cellin reference', 'nose_reference'), ('Nose aimed + second landmark directly above nose (roll reference)', 'nose_and_up'), ('Controlled roll sequence (no pitch/yaw control input)', 'roll_sequence')]:
            tk.Radiobutton(window,text=title,variable=mode,value=value).pack(anchor='w',padx=12)
        tk.Label(window, text='Next priority: record from two different diagonal sides, roughly level with the reference.\nThen collect varied heights. Aim the fixed ship nose, center freelook/headtracking,\nand hold position and attitude steady through the recording countdown.', justify='left').pack(pady=10)
        tk.Label(window,text=f'Primary reference (km): {REFERENCE}').pack()
        tk.Label(window,text='For nose + up only: enter the second landmark X, Y, Z in km, separated by commas.').pack(pady=(10,0))
        second = tk.Entry(window,width=65)
        second.pack()
        confirmed = tk.BooleanVar(value=False)
        tk.Checkbutton(window,text='Second landmark is directly above the fixed nose in a centered cockpit view',variable=confirmed).pack()
        phase = tk.StringVar(value='Starting upright')
        tk.Label(window,text='For a roll sequence: keep the same roll direction, pause at each phase, and record.').pack(pady=(10,0))
        tk.OptionMenu(window,phase,'Starting upright','Banked 90 degrees','Upside down','Banked 270 degrees','Upright again').pack()
        note = tk.Label(window,text='Ready. Each capture checks several readings over 3 seconds.',wraplength=660,justify='left')
        note.pack(pady=12)
        button = tk.Button(window,text='Record reference — 3 second capture')
        button.pack(pady=8)
        tk.Label(window,text='Saved beside this program: reference_samples.json\nAll three raw CamDir values, timestamps, capture readings and reference geometry are preserved.\nKeep the file and send it for analysis. No guessed correction is applied.',justify='left').pack(pady=10)

        def begin():
            if self.args.demo:
                note.config(text='Recording is disabled in demo mode.')
                return
            selected, phase_value = mode.get(), phase.get()
            second_point = None
            try:
                if selected == 'nose_and_up':
                    if not confirmed.get():
                        raise ValueError('Confirm the second landmark is above the fixed nose.')
                    second_point = vector([float(v.strip()) for v in second.get().split(',')])
            except ValueError as exc:
                note.config(text=str(exc))
                return
            button.config(state='disabled')
            readings = []
            deadline = time.monotonic()+3

            def capture():
                if not window.winfo_exists():
                    return
                try:
                    sample = reference_sample(json.loads(self.path.read_text(encoding='utf-8-sig')))
                    if not readings or any(sample[k] != readings[-1][k] for k in ('position_accepted_at','orientation_accepted_at')):
                        readings.append(sample)
                    remaining = deadline-time.monotonic()
                    if remaining > 0:
                        note.config(text=f'Hold steady… {remaining:.1f}s remaining. {len(readings)} reading pairs collected.')
                        window.after(200,capture)
                        return
                    if len({r['position_accepted_at'] for r in readings})<2 or len({r['orientation_accepted_at'] for r in readings})<2:
                        raise ValueError('Need at least two fresh position and orientation updates. Hold steady and retry.')
                    first = readings[0]
                    if any(math.dist(first['position_km'],r['position_km'])>.002 for r in readings):
                        raise ValueError('Position moved more than 2 metres during capture. Stop drifting and retry.')
                    if any(direction_error(rotate(axis,first['camdir_raw']),rotate(axis,r['camdir_raw']))>4 for r in readings for axis in ((0,1,0),(0,0,1))):
                        raise ValueError('Attitude changed more than 4 degrees. Pause the ship rotation while recording.')
                    result = enrich_sample(dict(readings[-1]),selected,second_point,phase_value)
                    result['capture_readings'] = readings
                    result['capture_duration_seconds'] = 3
                    count = append_reference_sample(Path(__file__).with_name('reference_samples.json'),result)
                    error = result.get('candidate_nose_error_degrees')
                    description = f' Candidate nose error: {error:.1f} degrees.' if error is not None else ''
                    note.config(text=f'Saved sample {count}.{description} This is a diagnostic, not an accuracy guarantee. Move to the next viewpoint or roll phase.')
                    self.record_note.config(text=f'Saved sample {count} in reference_samples.json',fg='#69ffe3')
                except (OSError,ValueError,TypeError) as exc:
                    note.config(text=f'Not recorded: {exc}')
                button.config(state='normal')
            capture()
        button.config(command=begin)

    def recenter(self):
        if self.display is not None:
            self.center = self.display

    def center_reference(self):
        self.follow.set(False)
        self.center = REFERENCE

    def reset_view(self):
        self.azimuth, self.elevation, self.span = -35., 35., .16*STATION_BASE_SCALE
        self.center_reference()

    def orbit(self, event):
        if self.drag:
            self.azimuth += (event.x - self.drag[0]) * .4
            self.elevation = max(-85., min(85., self.elevation + (event.y - self.drag[1]) * .4))
        self.drag = (event.x, event.y)

    def set_zoom(self, factor):
        self.span = max(.02, min(2000., self.span * factor))

    def zoom(self, event):
        self.set_zoom(.85 if event.delta > 0 else 1.18)

    def poll(self):
        try:
            if self.args.demo:
                t = time.monotonic() - self.started
                stamp = datetime.now(timezone.utc).isoformat()
                data = dict(position_km=[12 + math.sin(t / 12), -421 + math.cos(t / 12), 201 + .2 * math.sin(t / 7)], last_accepted_at=stamp, state='tracking', orientation=dict(components=[15 * math.sin(t / 5), 25 * math.sin(t / 4), t * 5], last_accepted_at=stamp, state='tracking'))
            else:
                data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('Status must be an object')
            position = vector(data['position_km']) if data.get('position_km') is not None else None
            orientation = data.get('orientation') or {}
            if not isinstance(orientation, dict):
                raise ValueError('Invalid orientation object')
            angles = vector(orientation['components']) if orientation.get('components') is not None else None
            self.status, self.error = data, ''
            stamp = data.get('last_accepted_at')
            if position is not None and stamp and stamp != self.last_stamp:
                gap = age(self.last_stamp) if self.last_stamp else math.inf
                if self.target is not None and (gap > 10 or math.dist(self.target, position) > max(5, self.span * 5)):
                    self.trail.append(None)
                    self.flight_samples.clear()
                    self.display = position
                self.target, self.last_stamp = position, stamp
                self.trail.append(position)
                self.flight_samples.append((time.monotonic(), position))
                if self.display is None:
                    self.display = position
            stamp = orientation.get('last_accepted_at')
            if angles is not None and stamp and stamp != self.orientation_stamp:
                self.angles, self.orientation_stamp = angles, stamp
                if self.display_angles is None:
                    self.display_angles = angles
        except (OSError, ValueError, TypeError) as exc:
            self.error = f'Waiting for readable status: {exc}'
        self.root.after(200, self.poll)

    def project(self, point):
        x, y, z = (point[i] - self.center[i] for i in range(3))
        a, e = math.radians(self.azimuth), math.radians(self.elevation)
        right = x * math.cos(a) - y * math.sin(a)
        depth = x * math.sin(a) + y * math.cos(a)
        up = z * math.cos(e) - depth * math.sin(e)
        scale = min(self.canvas.winfo_width(), self.canvas.winfo_height()) / (2 * self.span)
        return self.canvas.winfo_width() / 2 + right * scale, self.canvas.winfo_height() / 2 - up * scale

    def line(self, a, b, color, width=1):
        self.canvas.create_line(*self.project(a), *self.project(b), fill=color, width=width)

    def tick(self):
        now = time.monotonic()
        fraction = 1 - math.exp(-min(now - self.last_tick, .2) / .22)
        self.last_tick = now
        if self.target is not None:
            self.display = tuple(a + (b - a) * fraction for a, b in zip(self.display, self.target))
            if self.follow.get():
                self.center = self.display
        if self.angles is not None:
            # Coupled Euler branches must not be interpolated component by component.
            # Show the latest accepted attitude until quaternion interpolation is added.
            self.display_angles = self.angles
        self.draw()
        self.root.after(33, self.tick)

    def draw(self):
        c = self.canvas
        c.delete('all')
        pa, oa = age(self.last_stamp), age(self.orientation_stamp)
        state = self.status.get('state', 'waiting')
        stale = pa > 3 or state in ('stopped', 'write_error') or bool(self.error)
        def age_text(v):
            return 'unavailable' if not math.isfinite(v) else f'{v:.1f}s old'
        pos = 'Waiting for accepted position' if self.target is None else 'X {:.4f}   Y {:.4f}   Z {:.4f} km'.format(*self.target)
        ori = 'Orientation unavailable' if self.angles is None else 'CamDir A {:.1f}   B {:.1f}   C {:.1f}'.format(*self.angles)
        self.info.config(text=f'{"DEMO | " if self.args.demo else ""}{pos}    | {state} • {age_text(pa)}\n{ori}    | {age_text(oa)}', fg='#f8c16b' if stale else '#b7f9ed')
        if self.center is None:
            c.create_text(c.winfo_width()/2, c.winfo_height()/2, text='Waiting for the OCR tracker…\n' + str(self.path), fill='#b5ccdf', width=600, justify='center')
            return
        step = 10 ** math.floor(math.log10(self.span / 4))
        if self.span / step > 12:
            step *= 5
        cx, cy, cz = self.center
        for i in range(-12, 13):
            xx = math.floor(cx / step) * step + i * step
            yy = math.floor(cy / step) * step + i * step
            self.line((xx, cy-self.span*2, cz), (xx, cy+self.span*2, cz), '#172c3c')
            self.line((cx-self.span*2, yy, cz), (cx+self.span*2, yy, cz), '#172c3c')
        for axis, color, label in [(0, '#ed7979', '+X'), (1, '#8ddd96', '+Y'), (2, '#82baff', '+Z')]:
            end = list(self.center)
            end[axis] += self.span * .55
            self.line(self.center, end, color, 2)
            c.create_text(*self.project(end), text=label, fill=color, anchor='sw')
        self.draw_station()
        previous = None
        for point in self.trail:
            if point is not None and previous is not None:
                self.line(previous, point, '#398b9b', 2)
            previous = point
        rx, ry = self.project(REFERENCE)
        c.create_oval(rx-5, ry-5, rx+5, ry+5, fill='#ffc766', outline='white')
        c.create_text(rx+10, ry-10, anchor='sw', text='Cellin reference', fill='#ffc766')
        if self.display is None:
            c.create_text(14, 14, anchor='nw', text='Waiting for accepted position\n' + str(self.path), fill='#ffc766')
            return
        distance_m = math.dist(self.target, REFERENCE) * 1000
        c.create_text(14, c.winfo_height()-55, anchor='sw', text=f'Reference distance: {distance_m:.1f} m', fill='#ffc766')
        # Constant screen-size marker. Distances and trail remain in kilometres.
        size = self.span * .08
        angles = self.display_angles or (0, 0, 0)
        angles = tuple(-a if flag.get() else a for a, flag in zip(angles, self.signs))
        vertices = [(0, 1.6, 0), (-1, -1, 0), (0, -.5, .55), (1, -1, 0), (0, -.5, -.3)]
        points = [tuple(self.display[i] + size * v for i, v in enumerate(apply_alignment(rotate(p, angles), self.correction))) for p in vertices]
        color = '#f8c16b' if stale else '#69ffe3'
        for a, b in [(0,1),(0,2),(0,3),(0,4),(1,2),(2,3),(3,4),(4,1)]:
            self.line(points[a], points[b], color, 2)
        x, y = self.project(points[0])
        c.create_oval(x-3,y-3,x+3,y+3,fill='white',outline='')
        note = self.error or ('Last known position • tracker stopped or stale' if stale else 'Live position')
        if oa > 5:
            note += ' | Orientation stale / unavailable'
        c.create_text(14, 14, anchor='nw', text=note, fill='#f8c16b' if stale or oa > 5 else '#83b6c4', width=max(200,c.winfo_width()-28))
        c.create_text(14, c.winfo_height()-14, anchor='sw', text=f'Grid spacing {step:g} km • {self.calibration_note}\nLocal XY grid • ship size exaggerated • trail records position', fill='#83a0b5')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status-file', default=str(Path(__file__).with_name('tracking_status.json')))
    parser.add_argument('--demo', action='store_true', help='Run a synthetic flight without OCR')
    args = parser.parse_args()
    root = tk.Tk()
    Minimap(root, args)
    root.mainloop()


if __name__ == '__main__':
    main()
