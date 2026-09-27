Recorded session traces for the dashboard's Replay mode (`.jsonl`, one JSON event per line:
`{"t": unix_time, "topic": "captions"|"latency"|"gate", "payload": {...}}`).

Record one by opening the dashboard (http://localhost:8450), joining with mic + camera, clicking
"⏺ Record this session", talking to the agent for a bit, then clicking "⏹ Stop + save recording".
The file lands here automatically via `POST /api/replays/save` (see `web_client.py`).

No file exists here yet as of this commit — one real recording still needs to be made manually
(requires a live browser session with a real microphone/camera, which this environment cannot
drive itself). See the dashboard build report for details.
