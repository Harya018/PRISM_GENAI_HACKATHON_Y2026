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
environment cannot drive. In particular, whether Gemini accepts `language="en-IN"` at all can
only be confirmed by that live test; `GOOGLE_LANGUAGE=en-US` is the documented fallback if not.
