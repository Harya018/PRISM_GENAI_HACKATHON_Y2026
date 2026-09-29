#!/usr/bin/env python3
"""Minimal local test client for the device-troubleshooting extension — serves a page on
http://localhost:8450 that joins a LiveKit room with mic only (voice-first; see dashboard.html
for the full UI with photo support) and talks to extension/device_agent.py. No frontend
framework: plain HTML + the LiveKit JS SDK from a CDN.

Requests explicit agent dispatch (RoomAgentDispatch(agent_name="device-support")) when minting
the join token, so this can never collide with any FDB-v3 benchmark room even if one happens to
be registered at the same time — but per device_agent.py's own policy comment, don't actually
run this while a benchmark run is active; stop the benchmark agent first.

Usage: python extension/web_client.py
Then open http://localhost:8450 in a browser, grant mic permission, and talk.
"""
import http.server
import json
import os
import secrets
import socketserver
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from livekit import api
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.agent_dispatch import RoomAgentDispatch

PORT = int(os.getenv("WEB_CLIENT_PORT", "8450"))
ROOM_NAME = "device-support-test"
AGENT_NAME = "device-support"

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
# LiveKit JS SDKs expect an http(s) URL for their own internal use even though the actual
# transport is websocket — this mirrors how livekit-client's own examples derive it.
LIVEKIT_HTTP_URL = LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")

PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Device Support — test client</title>
<script src="https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js"></script>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; margin: 0;
        display: flex; flex-direction: column; align-items: center; padding: 24px; }}
  video {{ width: 320px; border-radius: 8px; background: #222; }}
  button {{ font-size: 16px; padding: 10px 20px; margin-top: 16px; border-radius: 6px;
          border: none; background: #4f8; cursor: pointer; }}
  button:disabled {{ background: #555; color: #999; }}
  #status {{ margin-top: 12px; color: #9cf; }}
  #log {{ width: 480px; max-height: 200px; overflow-y: auto; font-size: 12px; color: #888;
        margin-top: 12px; white-space: pre-wrap; }}
</style>
</head>
<body>
  <h2>Samsung Device Support — extension test</h2>
  <button id="joinBtn">Join with mic (voice-only)</button>
  <div id="status">Not connected</div>
  <div id="log"></div>

<script>
const statusEl = document.getElementById('status');
const logEl = document.getElementById('log');
function log(msg) {{
  logEl.textContent += msg + "\\n";
  logEl.scrollTop = logEl.scrollHeight;
}}

document.getElementById('joinBtn').onclick = async () => {{
  const joinBtn = document.getElementById('joinBtn');
  joinBtn.disabled = true;
  try {{
    statusEl.textContent = "Fetching token...";
    const resp = await fetch('/token');
    const {{ token, url }} = await resp.json();

    const room = new LivekitClient.Room();
    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {{
      if (track.kind === 'audio') {{
        const audioEl = track.attach();
        document.body.appendChild(audioEl);
      }}
      log('Subscribed to ' + track.kind + ' from ' + track.sid);
    }});
    room.on(LivekitClient.RoomEvent.ParticipantConnected, (p) => log('Agent joined: ' + p.identity));
    room.on(LivekitClient.RoomEvent.Disconnected, () => statusEl.textContent = "Disconnected");

    statusEl.textContent = "Connecting...";
    await room.connect(url, token);
    statusEl.textContent = "Connected — enabling mic...";

    await room.localParticipant.setMicrophoneEnabled(true);
    const micPub = room.localParticipant.getTrackPublication(LivekitClient.Track.Source.Microphone);
    log('Mic track published: ' + !!(micPub && micPub.track));

    statusEl.textContent = "Live — talk to the agent now (voice-only; use the main dashboard for photo support).";
    log('Joined room as ' + room.localParticipant.identity);
  }} catch (err) {{
    console.error('Join failed:', err);
    statusEl.textContent = "Error: " + (err && err.message ? err.message : String(err));
    joinBtn.disabled = false;
  }}
}};
</script>
</body>
</html>
"""


def mint_token() -> dict:
    identity = f"tester-{secrets.token_hex(4)}"
    # A fresh room name per join, not the fixed ROOM_NAME constant: explicit agent dispatch
    # (RoomAgentDispatch below) fires once, at room CREATION -- a participant who joins an
    # already-existing room (e.g. rejoining seconds after the last one left, before LiveKit's
    # empty-room grace period expires) gets no new dispatch and no agent ever arrives, even
    # though the join itself and the mic both work fine. This was found live: `participant_
    # connected` logged in device_agent.py with no `track_subscribed` ever following, sometimes
    # for hours, because the same "device-support-test" room kept getting silently reused.
    room_name = f"{ROOM_NAME}-{secrets.token_hex(4)}"
    grants = api.VideoGrants(
        room_join=True, room=room_name,
        can_publish=True, can_subscribe=True, can_publish_data=True,
    )
    room_config = RoomConfiguration(
        agents=[RoomAgentDispatch(agent_name=AGENT_NAME)],
    )
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_grants(grants)
        .with_room_config(room_config)
        .to_jwt()
    )
    return {"token": token, "url": LIVEKIT_URL}


DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.html"
DASHBOARD_DATA_PATH = Path(__file__).resolve().parent.parent / "docs" / "dashboard_data.json"
REPLAYS_DIR = Path(__file__).resolve().parent / "replays"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/results":
            # Read-only: serves the precomputed docs/dashboard_data.json (built by
            # scripts/build_dashboard_data.py from real result/eval files). Never triggers any
            # computation or benchmark run itself -- if the file is missing, says so plainly.
            if DASHBOARD_DATA_PATH.exists():
                body = DASHBOARD_DATA_PATH.read_bytes()
                self.send_response(200)
            else:
                body = json.dumps({"error": "docs/dashboard_data.json not found -- run "
                                            "scripts/build_dashboard_data.py first"}).encode("utf-8")
                self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/replays":
            names = sorted(p.name for p in REPLAYS_DIR.glob("*.jsonl")) if REPLAYS_DIR.exists() else []
            body = json.dumps(names).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/api/replays/"):
            name = path[len("/api/replays/"):]
            rf = REPLAYS_DIR / name
            if ".." in name or not rf.exists():
                self.send_response(404)
                self.end_headers()
                return
            body = rf.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/":
            body = DASHBOARD_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/simple":
            # the original minimal test client (mic+camera only, no dashboard panels) — kept as
            # a quick fallback for isolating a connection issue from a dashboard/rendering issue.
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/token":
            body = json.dumps(mint_token()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/replays/save":
            # Saves a client-recorded session (every data-channel event it received, with
            # relative timestamps) as JSONL for later replay — never triggers or affects any
            # LiveKit session; the recording itself happens entirely in the browser.
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
                events = payload.get("events", [])
                name = payload.get("name") or f"session_{secrets.token_hex(3)}"
                name = "".join(c for c in name if c.isalnum() or c in "-_") + ".jsonl"
                REPLAYS_DIR.mkdir(parents=True, exist_ok=True)
                with open(REPLAYS_DIR / name, "w", encoding="utf-8") as f:
                    for ev in events:
                        f.write(json.dumps(ev) + "\n")
                resp = json.dumps({"saved": name, "count": len(events)}).encode("utf-8")
                self.send_response(200)
            except Exception as e:
                resp = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # keep stdout clean; this is a throwaway local test server


if __name__ == "__main__":
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Serving on http://localhost:{PORT} — room '{ROOM_NAME}', "
             f"dispatching agent '{AGENT_NAME}'")
        httpd.serve_forever()
