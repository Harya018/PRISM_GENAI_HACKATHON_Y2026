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

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, RoomInputOptions, llm
from livekit.plugins import google

from agent.commit_gate import CommitGate
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
        with open("/tmp/device_tool_calls.log", "a") as f:
            f.write(json.dumps({"room": self.room_name,
                               "call": {"function": func_name, "args": args,
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
    model = google.realtime.RealtimeModel(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        voice=os.getenv("GOOGLE_VOICE", "Puck"),
    )

    turn = TurnTranscript()
    stager = LatencyStager(ctx.room)

    def on_committed(tool_name: str, args: dict) -> None:
        """Fires once a proposed call has survived the commit gate's buffer and is about to
        execute for real — the one safe point to speak an instant acknowledgement without
        risking it being for a value that then gets corrected (agent/commit_gate.py)."""
        stager.on_tool_committed()
        session.say(random.choice(ACK_PHRASES), allow_interruptions=True)

    gate = CommitGate(call_tool=lambda name, args: registry.call(name, **args),
                      tool_kinds=TOOL_KINDS, tool_schemas=TOOL_SCHEMAS,
                      buffer_ms=COMMIT_BUFFER_MS, confirm_required=CONFIRM_REQUIRED,
                      on_committed=on_committed)

    fnc_ctx = DeviceAssistantFnc(turn, gate, ctx.room.name)
    tools = llm.find_function_tools(fnc_ctx)
    session = AgentSession(llm=model, tools=tools)

    @session.on("user_input_transcribed")
    def on_user_input(msg: agents.voice.UserInputTranscribedEvent):
        turn.append(getattr(msg, "transcript", "") or "")

    @session.on("agent_state_changed")
    def on_agent_state(ev: agents.voice.AgentStateChangedEvent):
        if ev.new_state == "thinking":
            stager.on_turn_end()
        elif ev.new_state == "speaking":
            stager.on_speaking_start()
            turn.reset()

    # video_enabled=True subscribes to the participant's camera track and forwards frames to
    # the realtime model alongside audio — this is the one piece of this extension not yet
    # exercised against a real camera feed end-to-end; wiring is per the current livekit-agents
    # RoomInputOptions API (confirmed in source, not assumed), degrades to audio-only if no
    # video track is published.
    await session.start(room=ctx.room, agent=DeviceAgent(),
                        room_input_options=RoomInputOptions(video_enabled=True))


if __name__ == "__main__":
    agents.cli.run_app(server)
