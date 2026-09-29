# AI usage log

Every entry below is a real change made with AI assistance (Claude Code) in this repo, kept for
transparency about what was AI-assisted and what it actually did — not a marketing summary.
`patches/README.md` and `results/ABLATIONS.md` reference this file; it didn't exist yet in the
repo, so this is its first entry rather than an addition to a longer history.

## 2026-09-29 — Extension agent: reliable English voice input + voice/snapshot mode

Scope: `extension/` only (`device_agent.py`, `device_instructions.py`, `web_client.py`,
`dashboard.html`). Did not touch the benchmark agent (`agent/lk_agent.py`, `agent/instructions.py`)
or anything on the graded `run_fdb_v3.sh` path — confirmed via `git status` before committing
that only `extension/` files changed.

**Diagnosis (why the agent sometimes didn't hear the user), in the order checked:**
1. Worker/dispatch: `device_agent.py`'s own worker registers explicit dispatch
   `agent_name="device-support"`, matching `web_client.py`'s `RoomAgentDispatch(agent_name=
   "device-support")` — not the cause.
2. Gemini quota: grepped `device_agent_run5.log`/`run6.log` for `429`/`RESOURCE_EXHAUSTED`/
   `quota`/`GoAway` — zero hits in either. Not the cause for those sessions.
3. **Browser side (the confirmed candidate):** `dashboard.html`'s join handler had no
   `try`/`catch` anywhere. If `setMicrophoneEnabled(true)` throws (permission denied, no
   device, any `getUserMedia` error), the promise rejects as a silent unhandled rejection —
   the status text sticks at "Connected — enabling mic..." forever with zero visible error, and
   no mic track is ever published. This matches the reported symptom exactly and had no
   instrumentation to catch it.
4. Room side: no `track_subscribed` handler existed anywhere in `device_agent.py` — confirmed
   by `grep` returning zero matches. Real gap, now fixed.
5. Model side: `on_user_input`/`on_conversation_item` published to the data channel but never
   wrote to the agent's own log file, so a silent session left no server-side trace at all
   beyond LiveKit's own connection-level logs. Also confirmed no `session.on("error")`/`"close"`
   handler existed to surface a Gemini-side session failure.

One real crash was found in `device_agent_run5.log` unrelated to the above: a test session's
data channels closed unexpectedly ~80s after the agent linked to the participant (`"publisher
data channel '_reliable' closed unexpectedly"`), followed by `"job did not ack shutdown in
time"` — consistent with the browser tab/connection dropping, not a code defect.

**Changes made:**
- `dashboard.html` / `web_client.py`: wrapped the join flow in `try`/`catch`, surfacing any
  mic/connect error to the status line and console instead of failing silently; log whether the
  mic track actually published (`getTrackPublication(Track.Source.Microphone)`) rather than
  assuming success from a non-throwing call.
- `device_agent.py`: added `ctx.room.on("track_subscribed", ...)` and
  `ctx.room.on("participant_connected", ...)` logging (participant identity, track kind/sid,
  timestamp) to the agent's own log; added explicit `logger.info(...)` for every user input
  transcript and every agent output transcript, each with a timestamp; added `session.on(
  "error")`/`session.on("close")` handlers logging the underlying `LLMError`/`RealtimeModelError`
  (confirmed these event types and their `ErrorEvent`/`CloseEvent` shapes by reading
  `livekit.agents.voice.events` source directly, not from memory).
- Language: default changed from `en-US` to `en-IN` (`GOOGLE_LANGUAGE` env var still overrides).
  `google.genai.types` has no `LanguageCode` enum (checked: `hasattr` is `False`) and the
  plugin's `language` param on `RealtimeModel` is an unvalidated `NotGivenOr[str]` — acceptance
  of "en-IN" can only be confirmed by a live session, not static inspection; this is called out
  as an explicit open question for the live test below. Added `output_audio_transcription`
  alongside the pre-existing `input_audio_transcription` (previously only input was configured).
  Added "RESPOND IN ENGLISH. YOU MUST RESPOND UNMISTAKABLY IN ENGLISH..." to the very top of
  `device_instructions.py`'s system instructions (previously present but lower down and softer).
- Voice + single snapshot: removed continuous camera capture/publish from `dashboard.html` and
  `web_client.py`'s fallback page, and removed `video_input`/`video_enabled=True` from
  `device_agent.py`'s `RoomInputOptions` (voice-only by default now). Added a "Send photo"
  button (webcam capture or file upload via `<input type="file" accept="image/*"
  capture="environment">`, downscaled client-side to a 768px long edge, JPEG) sent over a
  LiveKit byte stream (topic `"snapshot"`) via `localParticipant.sendBytes` — confirmed this API
  and its `SendBytesOptions` shape against the actual `livekit-client` `.d.ts` (via jsdelivr),
  not memory. Server side registers `ctx.room.register_byte_stream_handler("snapshot", ...)`;
  confirmed `register_byte_stream_handler`'s callback is called synchronously (not awaited) by
  reading `livekit.rtc.room`'s dispatch source directly, so the handler hands off to an
  `asyncio.Task` rather than blocking or silently no-oping. The image is added to the
  conversation via `device_agent.chat_ctx.copy()` + `.add_message(role="user", content=[
  llm.ImageContent(image=data_url)])` + `await device_agent.update_chat_ctx(new_ctx)` — every
  one of those calls (`ChatContext.copy`, `ChatContext.add_message`, `llm.ImageContent`,
  `Agent.update_chat_ctx`) was confirmed to exist with this exact signature by reading the
  installed `livekit-agents` source via `inspect`, not guessed. `reset_network_settings`'s
  confirm-before-destructive gate in `agent/commit_gate.py` was not touched.
- UI: relabeled the conversation panel to "You said:"/"Agent:"; added a granular
  `#agentStatus` line driven by a new `"status"` data-channel topic (participant joined, track
  subscribed, agent state, photo received/failed, error, close) so the dashboard shows more than
  just "connected"; added a mic mute/unmute button.

**Not done by AI, left for the user:** the actual live 3-minute test sessions (voice-only with a
mid-sentence self-correction, then with a photo sent mid-conversation, then confirming the
`reset_network_settings` gate) — these require a real browser microphone/camera, which this
environment cannot drive.

## 2026-09-29 (same day, follow-up) — en-IN confirmed rejected live; reverted to en-US

The user's first live test after the change above reproduced exactly the failure this log
flagged as an open question: joining worked (mic published, agent joined the room, track
subscribed), but nothing was ever transcribed — the transcript panel stayed empty the whole
session. The new logging from the commit above made this diagnosable in under a minute:
`device_agent_run9.log` showed `google.genai.errors.APIError: 1007 None. Unsupported language
code 'en-IN' for model models/gemini-2.5-flash-native-audio-preview-12-2025`, immediately
followed by `AgentSession is closing due to unrecoverable error` — the whole Gemini session was
torn down before the entrypoint even finished starting, so the user was talking to a dead
session the entire time.

Fix: reverted the default `language` back to `"en-US"` (the value already confirmed working
before this task started); `GOOGLE_LANGUAGE` still overrides. Restarted the worker and confirmed
`agent_state_changed: initializing -> listening` with no error in `device_agent_run10.log` —
the session now actually connects.

## 2026-09-29 (second follow-up) — "sometimes listening, sometimes not"

The user reported the agent was inconsistent: sometimes it heard and transcribed, sometimes
nothing happened at all when they spoke. `device_agent_run10.log` showed the pattern precisely:
one join (06:13) worked; two rejoins two minutes apart (06:21, 06:22) each logged
`participant_connected` but **no `track_subscribed` ever followed, and no new `"registered
worker"`/session startup either** — then a 2.5-hour gap before the next working session at
08:53.

Root cause: `web_client.py`'s `mint_token()` used a fixed module-level `ROOM_NAME =
"device-support-test"` for every single join. LiveKit's explicit agent dispatch
(`RoomAgentDispatch`) fires once, at room *creation* — a participant who joins an
*already-existing* room (e.g. rejoining seconds after the previous one disconnected, before
LiveKit's empty-room grace period expires and tears the room down) gets no new dispatch, so no
agent job ever spins up for that join, even though the browser's mic and room connection both
work perfectly fine. This is exactly what the log showed: a live `Room` object receiving
`participant_connected` (proving the room still existed) with nothing downstream of it (proving
no job/AgentSession was attached to hear it).

Fix: `mint_token()` now generates a fresh room name per call
(`f"{ROOM_NAME}-{secrets.token_hex(4)}"`), so every "Join with mic" click creates a brand-new
room and is guaranteed a fresh explicit-dispatch job — this was not something that could be
partially mitigated; it needed a genuinely unique room per session. Restarted both
`device_agent.py` and `web_client.py` on the fix.

Also documented (not fixed — not fixable by more config, confirmed live): even with
`language="en-US"` and `language_codes=["en-US"]` set on both transcription configs, one working
session still transcribed accented English speech into Tamil and Japanese script. The installed
SDK's own field description for `AudioTranscriptionConfig.language_codes` calls it a "hint,"
not an enforced constraint — this is inherent auto-detection behavior in this preview
native-audio model (`gemini-2.5-flash-native-audio-preview-12-2025`), not a bug in this repo's
code. Left as a known limitation in `device_agent.py`'s own comment rather than guessed at
further.
