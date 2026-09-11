# Cellin multiplayer — Railway version

The desktop OCR and minimap stay on each player's PC. One Railway service
receives updates, returns the player roster and records the session. No VPN
or router port forwarding is required. This package is prepared for deployment;
it has not been deployed to your Railway account.

## Deploy the server

1. Extract this package. Put its files at the root of a new GitHub repository,
   or use this folder as your Railway service root. Do not commit a token.
2. In Railway, create a service from that repository. The supplied Dockerfile
   builds the server. Alternatively, deploy this folder with Railway's CLI.
3. Add a volume to this service, mounted at **/data**. Recordings go to
   `/data/recordings`. Without this volume, recordings are ephemeral.
4. Add service variables:
   - `SESSION_TOKEN`: a randomly generated secret of at least 32 characters.
   - `DATA_DIR`: `/data`.
   - `RAILWAY_DEPLOYMENT_DRAINING_SECONDS`: `15` (allows shutdown time).
5. Generate a token locally with:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   Copy it into Railway and share it privately with your friend. Do not put it
   into a URL, repository, screenshot or public message.
6. Deploy/redeploy after adding the variables and volume. Keep **one replica**
   and the supplied **one Gunicorn worker**. Session state lives in memory.
7. In Settings → Networking → Public Networking, generate a domain. Use its
   HTTPS URL, such as `https://your-service.up.railway.app`.
8. Open `https://your-service.up.railway.app/health`; it should return
   `{"status":"ok"}`. Authentication is still required for player data.

Railway provides HTTPS at its edge and forwards to Gunicorn listening on
`0.0.0.0:$PORT`. The Dockerfile/start configuration already handles this.
No TCP proxy, manually configured certificate or desktop window is needed.

Official setup references:
- https://docs.railway.com/builds/dockerfiles
- https://docs.railway.com/networking/public-networking
- https://docs.railway.com/volumes

## Connect both PCs

1. Extract the package on each PC; keep the Python files together.
2. Run each person's existing OCR tracker with `--orientation` (or
   `--orientation-focus` for diagnostic work).
3. Run `Start Multiplayer.bat`, or `python multiplayer.py`.
4. Enter your own name, the same Railway HTTPS base URL and session token.
5. Browse to your own `tracking_status.json`, then open the session.
6. Use Show all players if the ships are far apart. Both players should be
   in the same game instance near the same Cellin station.

Desktop networking needs no additional pip packages; use the Python/Tkinter
installation that runs your existing minimap. Gunicorn is installed only in
the Railway container. The OCR program is unchanged.

## Recording and lifecycle

Each server start creates a new `session_*.jsonl`. Restart/deploy resets the
live roster but preserves old files on the volume. Clients reconnect and
clear their old remote roster when the server's session ID changes. Inactive
identities expire after 120 seconds. There is a maximum of 16 active identities.
Recover recordings using Railway volume file tools; there is no publicly
accessible recording-download endpoint. Back up recordings and monitor volume
usage: automatic retention/deletion is intentionally not enabled.

Original independent capture timestamps, raw CamDir and positions, heartbeat
events and host receipt times are retained. Source sample ages compensate for
different PC wall-clock offsets when displaying stale status. Network delay is
not removed; this is not precision clock synchronization. Offline samples are
not queued for later delivery. Playback controls remain future work.

## Internet-facing controls and limits

HTTPS clients validate certificates and reject redirects so the token is not
forwarded to another endpoint. HTTP is allowed only on localhost/127.0.0.1 for
local testing. The server requires the token, limits updates to 16 KiB and
100 sync requests/second service-wide, validates incoming values, and uses
bounded Gunicorn threads/header limits. This is a small trusted-group service,
not a multi-tenant product or a DDoS-protected public API. Anyone with the
shared token can view the roster and submit player updates; individual account
authentication and identity ownership are not implemented. Rotate the token
if it is shared unintentionally.

The health route checks process availability, not whether disk recording is
healthy. Recording errors appear in the desktop connection status. The server
still forwards live data if a recording write fails.

## Local verification

For an optional local server test in PowerShell:

```powershell
$env:SESSION_TOKEN = 'replace-with-a-random-secret-at-least-32-characters'
$env:DATA_DIR = './local-data'
$env:PORT = '8765'
python server.py
```

Join `http://127.0.0.1:8765` from two launchers. This local-only test server
uses Python's WSGI reference server; Railway uses Gunicorn instead.

Verified locally: two-player WSGI/client integration, wrong-token rejection,
health route, invalid and oversized requests, URL restrictions, last-good data,
stale/disconnected flags, recording preservation and syntax. Docker/Gunicorn
deployment, real HTTPS service and native desktop UI still need an actual run.
The development runtime's Tk initialization issue prevents native UI validation.

## Version 3 orientation calibration

Both local and remote ships use the measured zero-angle forward/up frame.
Keep the camera centered and disable headtracking/freelook for ship attitude.
Raw OCR and network angles are unchanged; no yaw-only inversion is applied.
Use this package on both PCs. Existing server protocol is compatible. For new
session recordings to include the revised calibration metadata, deploy this
package to the server too. This revision has not been deployed automatically.
The zero frame is approximate and combined rotations need live validation.
