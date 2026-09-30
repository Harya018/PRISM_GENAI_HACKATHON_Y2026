#!/usr/bin/env python3
"""Samsung device-troubleshooting extension (Section B) — the same LiveKit agent architecture
as the FDB-v3 benchmark agent (agent/lk_agent.py), reused rather than rebuilt: the correction-
aware commit gate, the block-only resolver, and the disfluency-aware instruction discipline all
apply unchanged. What's new here is domain-specific: four device-support tools (one of them
destructive, gated by the commit gate's code-enforced confirmation check — see
agent/commit_gate.py), and a live camera feed so the agent can ground its diagnosis in what it
actually sees instead of only what's described in words.

One command to run it:
    GOOGLE_API_KEY=... LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
        python extension/device_agent.py start

Then connect any LiveKit room client (browser, mobile, or the frontend in progress) with a
camera enabled to talk to it.
"""

import asyncio
import base64
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# agent/ is a sibling package one level up — reuse the commit gate and resolver as-is rather than
# duplicating or forking them for this extension.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.genai import types as genai_types
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, RoomInputOptions, llm
from livekit.plugins import google

logger = logging.getLogger("device_agent")

from agent.commit_gate import CommitGate, _redact as redact_args
from mock_device_apis import DeviceAPIRegistry

if hasattr(llm, "function_tool"):
    ai_callable_decorator = llm.function_tool
else:
    ai_callable_decorator = llm.ai_callable

from device_instructions import DEVICE_AGENT_INSTRUCTIONS

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

COMMIT_BUFFER_MS = float(os.getenv("LK_COMMIT_BUFFER_MS", "400"))
registry = DeviceAPIRegistry()

# --- Extension-only config flags (Step 6, all default OFF/unset = behavior already tested) ---
EXT_SESSION_RESUMPTION = os.getenv("LK_EXT_SESSION_RESUMPTION", "").lower() in ("1", "true", "yes")  # X1
EXT_CONTEXT_COMPRESSION = os.getenv("LK_EXT_CONTEXT_COMPRESSION", "").lower() in ("1", "true", "yes")  # X1
EXT_MEDIA_RESOLUTION = os.getenv("LK_EXT_MEDIA_RESOLUTION")   # unset|LOW|MEDIUM|HIGH -- X2
EXT_PROACTIVITY = os.getenv("LK_EXT_PROACTIVITY", "").lower() in ("1", "true", "yes")  # X3
EXT_AFFECTIVE_DIALOG = os.getenv("LK_EXT_AFFECTIVE_DIALOG", "").lower() in ("1", "true", "yes")  # X3
_TOOL_TIMEOUT_RAW = os.getenv("LK_TOOL_TIMEOUT_S")   # H1, unset -> None (unbounded, unchanged)
TOOL_TIMEOUT_S = float(_TOOL_TIMEOUT_RAW) if _TOOL_TIMEOUT_RAW is not None else None
_MAX_CALLS_RAW = os.getenv("LK_MAX_CALLS_PER_TOOL")   # H1, unset -> None (unbounded, unchanged)
MAX_CALLS_PER_TOOL = int(_MAX_CALLS_RAW) if _MAX_CALLS_RAW is not None else None

TOOL_KINDS = {
    "lookup_manual": "read_only",
    "get_device_status": "read_only",
    "open_settings": "state_modifying",
    "reset_network_settings": "state_modifying",
}
TOOL_SCHEMAS = {
    "lookup_manual": {"args": {"topic": {"type": "string"}}},
    "get_device_status": {"args": {}},
    "open_settings": {"args": {"panel": {"type": "string"}}},
    "reset_network_settings": {"args": {}},
}
CONFIRM_REQUIRED = frozenset({"reset_network_settings"})


class TurnTranscript:
    """Same shape as agent/lk_agent.py's — accumulates the current turn's transcribed text so
    the commit gate/resolver and the confirmation check can see what the user actually said."""
    def __init__(self):
        self.text = ""

    def append(self, chunk: str):
        self.text = (self.text + " " + chunk).strip() if self.text else chunk

    def reset(self):
        self.text = ""


ACK_PHRASES = ["Got it, checking that.", "One sec, looking into it.", "Okay, checking now."]


class LatencyStager:
    """Per-turn latency instrumentation, streamed live over the room's data channel so a
    frontend can light up Mic -> Transcript -> ... -> Reply as each stage actually happens
    (Section D, tomorrow — this only publishes events, the UI consuming them is separate and
    entirely optional; publishing never blocks or affects the agent's own behavior).

    Stages, all measured from end-of-turn (when the agent stops listening and starts reasoning):
      turn_end -> first_audio   (the instant "checking that" acknowledgement's own latency)
      turn_end -> tool_call     (when a proposed tool call survives the commit gate's buffer
                                  and is about to execute for real — agent/commit_gate.py's
                                  on_committed hook)
      turn_end -> answer        (the real, second speaking turn — after the tool result is in)
    """

    def __init__(self, room):
        self._room = room
        self._turn_end_ts: Optional[float] = None
        self._speaking_count = 0

    def _publish(self, stage: str, elapsed_ms: Optional[float]) -> None:
        payload = json.dumps({"stage": stage, "elapsed_ms": elapsed_ms,
                              "t": time.time()}).encode("utf-8")
        try:
            asyncio.ensure_future(
                self._room.local_participant.publish_data(payload, reliable=True,
                                                          topic="latency"))
        except Exception:
            pass  # never let telemetry publishing affect the agent's own behavior

    def on_turn_end(self) -> None:
        self._turn_end_ts = time.time()
        self._speaking_count = 0
        self._publish("turn_end", 0.0)

    def on_speaking_start(self) -> None:
        if self._turn_end_ts is None:
            return
        self._speaking_count += 1
        elapsed = (time.time() - self._turn_end_ts) * 1000
        stage = "first_audio" if self._speaking_count == 1 else "answer"
        self._publish(stage, round(elapsed, 1))

    def on_tool_committed(self) -> None:
        if self._turn_end_ts is None:
            return
        elapsed = (time.time() - self._turn_end_ts) * 1000
        self._publish("tool_call", round(elapsed, 1))


class DeviceAssistantFnc:
    def __init__(self, turn: TurnTranscript, gate: CommitGate, room_name: str):
        self.room_name = room_name
        self.turn = turn
        self.gate = gate

    def _log_tool_call(self, func_name, args, t_start, t_end):
        # H1: same redaction CommitGate applies to its own log -- this file writes the model's
        # raw proposed args (pre-resolver), a separate log stream, so it needs its own redaction
        # rather than relying on CommitGate's. No sensitive args exist in this extension's own
        # tools today; applied anyway so a future tool with one doesn't silently write it to disk.
        with open("/tmp/device_tool_calls.log", "a") as f:
            f.write(json.dumps({"room": self.room_name,
                               "call": {"function": func_name, "args": redact_args(args),
                                        "timestamp_start": t_start, "timestamp_end": t_end}}) + "\n")

    async def _call(self, name: str, **args):
        t_start = time.time()
        result = await self.gate.propose(name, args, turn_transcript=self.turn.text)
        t_end = time.time()
        self._log_tool_call(name, args, t_start, t_end)
        return json.dumps(result)

    @ai_callable_decorator(description="Look up a troubleshooting instruction by topic (e.g. "
                                       "wifi, bluetooth, battery, screen, update, storage, "
                                       "camera, network). Always call this rather than "
                                       "answering from memory.")
    async def lookup_manual(self, topic: str):
        """Args: topic: a short keyword for what the user is having trouble with."""
        return await self._call("lookup_manual", topic=topic)

    @ai_callable_decorator(description="Get the device's current diagnostic status (battery, "
                                       "Wi-Fi/Bluetooth connection, storage, software version). "
                                       "Always call this rather than guessing the device's state.")
    async def get_device_status(self):
        return await self._call("get_device_status")

    @ai_callable_decorator(description="Open a settings panel on the device (low-risk, "
                                       "navigational — safe to call directly once the panel "
                                       "name is settled).")
    async def open_settings(self, panel: str):
        """Args: panel: the settings panel to open, e.g. 'Wi-Fi' or 'Battery'."""
        return await self._call("open_settings", panel=panel)

    @ai_callable_decorator(description="Reset the device's network settings (Wi-Fi, Bluetooth, "
                                       "mobile data) to factory defaults. DESTRUCTIVE — only "
                                       "call this after the user has explicitly confirmed they "
                                       "want to proceed, per your instructions. Calling it "
                                       "without confirmation on record will be refused.")
    async def reset_network_settings(self):
        return await self._call("reset_network_settings")


class DeviceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=DEVICE_AGENT_INSTRUCTIONS)


server = AgentServer(load_threshold=0.95, num_idle_processes=1, port=8091)
# port=8091: a benchmark agent process may also be running and already holds the default 8081.
#
# POLICY: never run this extension agent while any FDB-v3 benchmark run (baseline or ours) is
# active, even though explicit dispatch (below) makes room-assignment collisions structurally
# impossible — the two still compete for this single machine's CPU, and a benchmark run's own
# reliability margin (auto-recovery notwithstanding) shouldn't be spent on it. Stop any running
# benchmark agent first.


@server.rtc_session(agent_name="device-support")
# agent_name enables EXPLICIT dispatch: this worker only picks up rooms that specifically
# request "device-support" (see extension/web_client.py's token minting) — it can never be
# handed a benchmark room, and a benchmark agent (fdb-ours/fdb-baseline) can never be handed
# this extension's room, regardless of what else happens to be registered at the same time.
async def entrypoint(ctx: agents.JobContext):
    model_kwargs = dict(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        voice=os.getenv("GOOGLE_VOICE", "Puck"),
        # Explicit English, both for the model's own responses (language=) and for input+output
        # transcription (*_audio_transcription=): without this, auto language detection was
        # picking Hindi for an Indian accent and transcribing English speech in Devanagari
        # script — found via a live test, not a hypothetical.
        #
        # google.genai.types has no LanguageCode enum (checked: hasattr is False) -- the
        # plugin's `language` param is an unvalidated NotGivenOr[str], so acceptance can only be
        # confirmed by a live session, not static inspection. "en-IN" WAS tried first, live, and
        # is rejected outright: the model closes the whole session before anything is heard
        # (google.genai.errors.APIError: "1007 ... Unsupported language code 'en-IN' for model
        # models/gemini-2.5-flash-native-audio-preview-12-2025" -- see device_agent_run9.log).
        # Falling back to "en-US" per the task's own fallback rule; GOOGLE_LANGUAGE still
        # overrides if a future model version adds en-IN support.
        #
        # Confirmed limitation, not fixable by more config (two separate live tests): the
        # installed SDK's own field description for AudioTranscriptionConfig.language_codes
        # says it provides "hints about the languages present in the audio" -- a hint, not an
        # enforced constraint. A live session with language_codes=["en-US"] produced Devanagari-/
        # Tamil-script transcripts of accented English speech; a second test with the bare code
        # language_codes=["en"] (GOOGLE_TRANSCRIPTION_LANGUAGE) made no difference -- still
        # Devanagari (device_agent_run10.log, device_agent_run12.log). Note this is a *caption*
        # issue only: the agent's own understanding and spoken replies were correct, fluent
        # English throughout both tests -- it heard and answered correctly even while the "you
        # said" transcript rendered in the wrong script. Reverted to the documented "en-US" form
        # since the experiment showed no benefit over it.
        language=(_lang := os.getenv("GOOGLE_LANGUAGE", "en-US")),
        input_audio_transcription=genai_types.AudioTranscriptionConfig(
            language_codes=[os.getenv("GOOGLE_TRANSCRIPTION_LANGUAGE", "en-US")]),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(
            language_codes=[os.getenv("GOOGLE_TRANSCRIPTION_LANGUAGE", "en-US")]),
    )

    # X1: session longevity. Gemini Live audio+video sessions cap at ~2 minutes without
    # compression and the underlying connection at ~10 minutes; both are opt-in so the
    # already-tested short-session behavior is unaffected until these are deliberately enabled
    # and a multi-minute session is tried live.
    if EXT_CONTEXT_COMPRESSION:
        model_kwargs["context_window_compression"] = genai_types.ContextWindowCompressionConfig(
            sliding_window=genai_types.SlidingWindow())
    if EXT_SESSION_RESUMPTION:
        model_kwargs["session_resumption"] = genai_types.SessionResumptionConfig()

    # X2: video cost/latency. media_resolution=LOW reduces per-frame token cost on the model
    # side; the client-side ~1fps/320x240 throttle (extension/dashboard.html) stays as the
    # actual frame-rate control regardless, since this livekit-agents version still has no
    # server-side video sampler to throttle capture rate itself.
    if EXT_MEDIA_RESOLUTION in ("LOW", "MEDIUM", "HIGH"):
        model_kwargs["media_resolution"] = getattr(
            genai_types.MediaResolution, f"MEDIA_RESOLUTION_{EXT_MEDIA_RESOLUTION}")

    # X3: 2.5-only tone/attention behaviors -- proactivity ignores background/off-device speech,
    # affective dialog gives a calmer tone for a frustrated user. The plugin switches to v1alpha
    # automatically when either is set (confirmed via the installed SDK's own NotGivenOr typing
    # on these two params). Off by default: keep only if a live test shows tool-calling doesn't
    # regress.
    if EXT_PROACTIVITY:
        model_kwargs["proactivity"] = True
    if EXT_AFFECTIVE_DIALOG:
        model_kwargs["enable_affective_dialog"] = True

    model = google.realtime.RealtimeModel(**model_kwargs)

    turn = TurnTranscript()
    stager = LatencyStager(ctx.room)

    def _publish(topic: str, payload: dict) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        try:
            asyncio.ensure_future(
                ctx.room.local_participant.publish_data(data, reliable=True, topic=topic))
        except Exception:
            pass  # telemetry only -- never affect agent behavior

    # --- Part 1 diagnostics: log every step of "does the agent actually hear the user" to the
    # agent's own log (not just the data-channel captions a frontend may or may not be
    # listening to), each with a timestamp, so a silent session can be root-caused from the log
    # alone -- room-side track subscription, then model-side transcripts/errors below.
    def _on_track_subscribed(track, publication, participant) -> None:
        logger.info("track_subscribed: participant=%s kind=%s sid=%s t=%s",
                    participant.identity, track.kind, track.sid, time.time())
        _publish("status", {"event": "track_subscribed", "kind": str(track.kind),
                            "participant": participant.identity, "t": time.time()})

    def _on_participant_connected(participant) -> None:
        logger.info("participant_connected: identity=%s t=%s", participant.identity, time.time())
        _publish("status", {"event": "participant_connected", "participant": participant.identity,
                            "t": time.time()})

    ctx.room.on("track_subscribed", _on_track_subscribed)
    ctx.room.on("participant_connected", _on_participant_connected)

    def on_committed(tool_name: str, args: dict) -> None:
        """Fires once a proposed call has survived the commit gate's buffer and is about to
        execute for real — the one safe point to speak an instant acknowledgement without
        risking it being for a value that then gets corrected (agent/commit_gate.py)."""
        stager.on_tool_committed()
        session.say(random.choice(ACK_PHRASES), allow_interruptions=True)

    def on_gate_event(event: str, entry: dict) -> None:
        """Streams every commit-gate log entry (buffered/superseded/executed/blocked_duplicate/
        confirmation_required/confirmed/resolver_corrected_arg/...) to the dashboard's
        commit-gate panel and correction timeline. Never affects agent behavior — publish
        failures are swallowed, same policy as LatencyStager._publish."""
        payload = json.dumps({"event": event, **{k: v for k, v in entry.items()
                                                 if k != "event"}},
                             default=str).encode("utf-8")
        try:
            asyncio.ensure_future(
                ctx.room.local_participant.publish_data(payload, reliable=True, topic="gate"))
        except Exception:
            pass

    gate = CommitGate(call_tool=lambda name, args: registry.call(name, **args),
                      tool_kinds=TOOL_KINDS, tool_schemas=TOOL_SCHEMAS,
                      buffer_ms=COMMIT_BUFFER_MS, confirm_required=CONFIRM_REQUIRED,
                      on_committed=on_committed, on_event=on_gate_event,
                      tool_timeout_s=TOOL_TIMEOUT_S, max_calls_per_tool=MAX_CALLS_PER_TOOL)

    fnc_ctx = DeviceAssistantFnc(turn, gate, ctx.room.name)
    tools = llm.find_function_tools(fnc_ctx)
    # Diagnostic: found a live session where the agent hallucinated "I'm having difficulty
    # accessing X" instead of ever actually calling a tool -- zero buffered/executed events in
    # the whole conversation, confirmed from the log. Tool registration itself tested fine in
    # isolation, so this one line rules that specific cause in/out instantly from the log alone
    # next time, instead of needing an isolated repro script to check it after the fact.
    logger.info("tools_registered: count=%d names=%s", len(tools),
               [getattr(t.info, "name", "?") for t in tools])
    session_kwargs = {}
    if EXT_MEDIA_RESOLUTION:
        # X2, corrected finding: a real server-side video sampler DOES exist in the installed
        # livekit-agents version (AgentSession(video_sampler=...), confirmed by reading the SDK
        # source directly, not assumed) -- our earlier conclusion that no such control existed
        # was wrong. Wire it in as a genuine server-side throttle: speak while the user talks
        # gets a normal 1fps, silence drops to 0.2fps. The client-side ~1fps/320x240 capture
        # throttle (extension/dashboard.html) stays regardless, since that one also cuts upload
        # bandwidth, which this sampler alone doesn't. Moot since Part 3 made video_enabled=False
        # the default below -- kept inert for whoever re-enables video_input deliberately.
        from livekit.agents.voice.agent_session import VoiceActivityVideoSampler
        session_kwargs["video_sampler"] = VoiceActivityVideoSampler(speaking_fps=1.0, silent_fps=0.2)
    session = AgentSession(llm=model, tools=tools, **session_kwargs)

    @session.on("user_input_transcribed")
    def on_user_input(msg: agents.voice.UserInputTranscribedEvent):
        text = getattr(msg, "transcript", "") or ""
        is_final = getattr(msg, "is_final", True)
        # Only append the CONFIRMED transcript to the turn buffer the gate/resolver sees --
        # interim (is_final=False) revisions are the ASR's evolving best guess for the same
        # utterance, not additional text; appending those too would garble turn.text with
        # overlapping partial repeats (a real bug fixed here, not just a caption-UI one).
        if is_final:
            turn.append(text)
        logger.info("user_input_transcribed: is_final=%s text=%r t=%s",
                    is_final, text, time.time())
        _publish("captions", {"speaker": "user", "text": text, "is_final": is_final,
                             "t": time.time()})

    @session.on("conversation_item_added")
    def on_conversation_item(ev) -> None:
        """Agent-side caption {speaker: "agent", text, is_final: true, t} -- user captions come
        from user_input_transcribed above (it has real interim/is_final data; this event doesn't
        fire until an item is fully committed, so using it for the user side too would lose the
        interim updates the dashboard needs). Additive; doesn't affect what the agent says."""
        item = ev.item
        if getattr(item, "role", None) != "assistant":
            return
        content = getattr(item, "content", None)
        text = " ".join(c for c in content if isinstance(c, str)) if isinstance(content, list) \
            else (content or "")
        if not text:
            return
        logger.info("agent_output_transcribed: text=%r t=%s", text, time.time())
        _publish("captions", {"speaker": "agent", "text": text, "is_final": True,
                             "t": time.time()})

    @session.on("agent_state_changed")
    def on_agent_state(ev: agents.voice.AgentStateChangedEvent):
        logger.info("agent_state_changed: %s -> %s t=%s", ev.old_state, ev.new_state, time.time())
        _publish("status", {"event": "agent_state", "state": str(ev.new_state), "t": time.time()})
        if ev.new_state == "thinking":
            stager.on_turn_end()
        elif ev.new_state == "speaking":
            stager.on_speaking_start()
            turn.reset()

    @session.on("error")
    def on_session_error(ev) -> None:
        # Model-side failures (Gemini session never activating, quota, a rejected `language`
        # value, etc.) surface here rather than as a silent hang -- this is the log signature
        # Part 1's diagnosis step 5 depends on.
        logger.error("session_error: source=%r error=%r t=%s", ev.source, ev.error, time.time())
        _publish("status", {"event": "error", "detail": repr(ev.error), "t": time.time()})

    @session.on("close")
    def on_session_close(ev) -> None:
        logger.warning("session_close: reason=%s error=%r t=%s", ev.reason, ev.error, time.time())
        _publish("status", {"event": "close", "reason": str(ev.reason), "t": time.time()})

    # --- Part 3: voice + single snapshot, not a continuous camera feed. The client sends at
    # most one still frame per "Send photo" click over a byte stream (topic "snapshot",
    # extension/dashboard.html); Room.register_byte_stream_handler's own callback contract is
    # synchronous (confirmed by reading livekit.rtc.room's dispatch source directly: it calls
    # the handler and does not await it), so the actual async work is handed to a task.
    device_agent = DeviceAgent()

    async def _handle_snapshot(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        try:
            chunks = [chunk async for chunk in reader]
            data = b"".join(chunks)
            mime_type = reader.info.mime_type or "image/jpeg"
            b64 = base64.b64encode(data).decode("ascii")
            data_url = f"data:{mime_type};base64,{b64}"
            # Confirmed via direct source inspection (not guessed): ChatContext.copy() +
            # .add_message(role=..., content=[...]) build the new context, llm.ImageContent
            # accepts a data: URL directly, and Agent.update_chat_ctx forwards to the active
            # realtime session for the Gemini model specifically.
            new_ctx = device_agent.chat_ctx.copy()
            new_ctx.add_message(role="user", content=[llm.ImageContent(image=data_url)])
            await device_agent.update_chat_ctx(new_ctx)
            logger.info("snapshot_received: participant=%s bytes=%d mime=%s t=%s",
                        participant_identity, len(data), mime_type, time.time())
            _publish("status", {"event": "photo_received", "bytes": len(data), "t": time.time()})
        except Exception as e:
            logger.error("snapshot_handling_failed: %r t=%s", e, time.time())
            _publish("status", {"event": "photo_failed", "detail": repr(e), "t": time.time()})

    def _on_snapshot_stream(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        asyncio.ensure_future(_handle_snapshot(reader, participant_identity))

    ctx.room.register_byte_stream_handler("snapshot", _on_snapshot_stream)

    # Voice-only by default -- no continuous camera publish/subscribe (Part 3). A photo the user
    # sends arrives over the "snapshot" byte stream above instead, one frame at a time.
    await session.start(room=ctx.room, agent=device_agent,
                        room_input_options=RoomInputOptions(video_enabled=False))
    _publish("status", {"event": "agent_ready", "t": time.time()})


if __name__ == "__main__":
    agents.cli.run_app(server)
