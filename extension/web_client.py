#!/usr/bin/env python3
"""Minimal local test client for the device-troubleshooting extension — serves a page on
http://localhost:8450 that joins a LiveKit room with mic + camera and talks to
extension/device_agent.py. No frontend framework: plain HTML + the LiveKit JS SDK from a CDN.

Requests explicit agent dispatch (RoomAgentDispatch(agent_name="device-support")) when minting
the join token, so this can never collide with any FDB-v3 benchmark room even if one happens to
be registered at the same time — but per device_agent.py's own policy comment, don't actually
run this while a benchmark run is active; stop the benchmark agent first.

Usage: python extension/web_client.py
Then open http://localhost:8450 in a browser, grant mic/camera permission, and talk.
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
  <video id="localVideo" autoplay muted playsinline></video>
  <button id="joinBtn">Join with mic + camera</button>
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
  document.getElementById('joinBtn').disabled = true;
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
  statusEl.textContent = "Connected — enabling mic + camera...";

  await room.localParticipant.setMicrophoneEnabled(true);
  // Reduced resolution + ~1 fps: the current livekit-agents version has no server-side video
  // sampler, so throttling at capture time (here) is what actually controls what Gemini
  // receives — a lower publish rate/resolution IS a lower received rate/resolution, not just a
  // bandwidth saving. frameRate is a hint (browsers vary in how strictly they honor a max), not
  // a hard guarantee — untested against a live camera as of this writing.
  await room.localParticipant.setCameraEnabled(true, {{
    resolution: {{ width: 320, height: 240, frameRate: 1 }},
  }});

  const camPub = room.localParticipant.getTrackPublication(LivekitClient.Track.Source.Camera);
  if (camPub && camPub.track) {{
    camPub.track.attach(document.getElementById('localVideo'));
  }}

  statusEl.textContent = "Live — talk to the agent now.";
  log('Joined room as ' + room.localParticipant.identity);
}};
</script>
</body>
</html>
"""


def mint_token() -> dict:
    identity = f"tester-{secrets.token_hex(4)}"
    grants = api.VideoGrants(
        room_join=True, room=ROOM_NAME,
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


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
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

    def log_message(self, format, *args):
        pass  # keep stdout clean; this is a throwaway local test server


if __name__ == "__main__":
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Serving on http://localhost:{PORT} — room '{ROOM_NAME}', "
             f"dispatching agent '{AGENT_NAME}'")
        httpd.serve_forever()
