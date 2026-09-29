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

## 2026-09-29 (third follow-up) — one more transcription-hint experiment, then reverted

User's next live test (after the room-name fix) confirmed the agent now reliably hears and
responds correctly: the agent's own replies were fluent, correct English throughout
("Hello! I can help with that. What seems to..." / "I understand. What specific issue are you
experiencing?"). The only remaining problem was the "you said" caption still rendering in
Devanagari/Tamil script for what was clearly English speech underneath (e.g. "हे माय लैपटॉप हैज
सम इशू" is literally "Hey my laptop has some issue," phonetically respelled).

Tried one cheap, reversible experiment: a bare `"en"` transcription hint instead of `"en-US"`
(added `GOOGLE_TRANSCRIPTION_LANGUAGE`, decoupled from `GOOGLE_LANGUAGE` so it couldn't risk
another 1007 connection rejection — that field validates strictly, the transcription hint
doesn't). Restarted, user spoke again: transcript came back as `'चार दिन मत दे'` — still
Devanagari, no improvement (`device_agent_run12.log`). This confirms the hint field genuinely
has no material effect on this preview model's script selection for this voice, not just that
`"en-US"` specifically was wrong. Reverted `GOOGLE_TRANSCRIPTION_LANGUAGE`'s default back to
`"en-US"` (the documented BCP-47 form) since the bare form showed no benefit. Restarted again,
confirmed clean startup, all 38 tests still pass.

**Net conclusion, stated plainly for the record:** the agent's hearing/understanding/response
pipeline works correctly end to end. The on-screen "you said" transcript caption's script is a
cosmetic display limitation of this specific preview model's ASR for this accent, not fixable
by any parameter this SDK version exposes — confirmed by two independent live experiments, not
assumed.

## 2026-09-29 — Submission audit against the Theme 5 participant guide, and results/ fix

User pasted the full participant guide and asked whether this submission matches it. Audited
the repo against every checklist item. Solid: LiveKit agent on the official templates,
one-command reproduction script, declared provider, README (architecture/setup/extension all
covered), and no compliance violations (grepped `agent/`/`extension/` for hardcoded
scenario refs — none; no external server calls beyond LiveKit/Google; `CommitGate` constructed
fresh per job, so nothing leaks across scenarios).

Found three real gaps: no demo video anywhere in the repo or the wider project folder; the
slide deck (`docs/CollegeName_TeamName_Submission.pptx`, outside this repo) is still the
unfilled organizer template — confirmed by extracting its slide XML text directly rather than
assuming from the filename, slide 1 has literal placeholder dashes ("Team Name -"); and
`results/ours/`/`results/baseline/` were empty directories, never committed, even though the
real best-run data existed in an uncommitted sibling checkout (`Full-Duplex-Bench/v3/results/`).

Fixed the results gap (the other two aren't code tasks): copied the real best-run reports
(`ours`: 100/100 complete, 86.4% tool-selection accuracy; `baseline`: 51/100, zero coverage in
two domains) into `results/{ours,baseline}/20260927_overnight_full/`, each with a
`run_config.json` stating provider, agent script, judge, and completion status honestly —
including flagging that a per-run seed wasn't independently recorded for this specific run
rather than inventing one, and explicitly noting these are exact-match (`judge=none`) scores,
not the official gpt-4o judge the graded re-run actually uses. Added `results/README.md`
(index) and `results/OVERNIGHT_REPORT.md` (the detailed breakdown that run already had).

Also found and fixed a real code bug while doing this: `scripts/build_dashboard_data.py`'s
default `--fdb-root` pointed at a sibling folder (`../../Full-Duplex-Bench/v3`) that only
exists on this specific development machine. `scripts/run_fdb_v3.sh` (the actual one-command
reproduction script) clones FDB-v3 into `fdb-agent/.fdb-v3/v3` instead, so the README's
documented "regenerate the dashboard after a new run" step would silently look in the wrong
place on a fresh checkout. Fixed the default to check `.fdb-v3/v3` first, fall back to the
sibling path second. Verified by re-running it: `docs/dashboard_data.json` regenerates
byte-for-byte identical (`git diff` empty).

**Deliberately not touched**: there's a deeper mismatch between `run_fdb_v3.sh`'s own
evaluation-output filenames/location and what `build_dashboard_data.py` expects, which would
need editing `run_fdb_v3.sh` itself to fully close. Per the standing rule to ask before changing
anything on the graded reproduction/evaluation path, this was surfaced to the user rather than
fixed without asking.

## 2026-09-29 — Harness hardening (H1) pulled from another team's architecture write-up

User pasted another Theme-5 team's project README (speculative retrieval, checkpoint-based
interruption, a goal stack for topic swerves, an ephemeral filler channel, and a harness with
admission control/timeouts/argument redaction) and asked which ideas were worth pulling in.
Implemented three, in the shared `agent/commit_gate.py` (used by both the benchmark agent and
the extension, so one change benefits both):

- **Argument redaction**: every event `CommitGate` logs or streams (`self.log`, `on_event` —
  our own telemetry, never what actually reaches `call_tool`) now redacts any arg whose *name*
  matches a sensitive pattern (`doc_number`, `account`, `ssn`, `password`, `card_number`, `cvv`,
  `pin`, `secret`, `api_key`, `token`). Name-pattern based rather than a hardcoded per-tool list,
  so it also covers the extension's tools without upkeep. Always on — pure logging hardening,
  zero change to what executes or what FDB-v3 scores.
- **`tool_timeout_s`** (opt-in via `LK_TOOL_TIMEOUT_S`, default unbounded): wraps an *async*
  `call_tool`'s await in `asyncio.wait_for`. Scoped honestly: none of this codebase's mock tools
  are actually async today, so this is a forward-looking safety net, documented as exactly that
  rather than oversold as protecting today's synchronous calls.
- **`max_calls_per_tool`** (opt-in via `LK_MAX_CALLS_PER_TOOL`, default unbounded): admission
  control against a runaway loop of genuinely-different-argument calls to the same tool —
  distinct from the pre-existing exact-repeat dedupe. Counts only committed (actually-executed)
  calls, so a long same-tool self-correction chain never trips it on its own.
- **Real bug fixed while adding the timeout**: a non-cancellation exception from `call_tool`
  (including a newly-possible timeout) used to leave a *superseded* caller's future permanently
  incomplete — that caller would hang forever awaiting a future nothing ever completes. Now the
  exception is also set on that future, so it re-raises instead of hanging.

**Investigated and deliberately NOT adopted** (documented directly in `commit_gate.py`'s own
module docstring, not just here): starting a read-only tool call speculatively, in the
background, the instant it's proposed — before the buffer window confirms it won't be
superseded — to overlap the tool's own latency with the correction-detection buffer. Checked
`Full-Duplex-Bench/v3/mock_apis.py` directly before implementing anything: `MockAPIRegistry.call()`
logs every invocation into a `CallLogger` the instant `call_tool` runs, and that log is what
FDB-v3 reads back as `actual_tool_calls` for scoring. Speculating means invoking `call_tool`
before knowing whether a call will be superseded — which would get the superseded call
permanently recorded and penalized as an "extra call," exactly the failure mode the whole
commit-gate architecture exists to prevent. Safe for a pure information-retrieval system with no
external logging tied to invocation (which is what the other team's own system is); not safe
here, where the act of calling itself is what gets scored. Caught this before writing any of
the risky code, not after.

7 new tests added (`tests/test_commit_gate.py`, 45 total, up from 38) covering: redaction
reaches `call_tool` unredacted but never the log; redaction on the `superseded`/`buffered`
events too; timeout is a no-op by default even for a slow async tool; a timeout fires and does
not hang a superseded waiter; the call budget is unbounded by default; the budget refuses past
its ceiling; and the budget does not count superseded calls. All 45 pass. Both flags wired
opt-in, unset by default, in both `agent/lk_agent.py` and `extension/device_agent.py` — the
extension worker was restarted on this code and registered cleanly.
