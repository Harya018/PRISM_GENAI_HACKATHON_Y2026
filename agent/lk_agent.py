#!/usr/bin/env python3
"""Our LiveKit voice agent for FDB-v3 — the stock `lk_agent_tool.py` template (native realtime
models) extended with: a correction-aware commit gate (agent/commit_gate.py), the disfluency-
aware system instructions (agent/instructions.py), and a configurable Gemini Live turn-detection
silence window (LK_SILENCE_MS) for the endpointing sweep (task A2).

Deployed by scripts/run_fdb_v3.sh, which copies this file plus commit_gate.py, resolver.py, and
instructions.py alongside the benchmark's own mock_apis.py/latency_injector.py in a checked-out
Full-Duplex-Bench/v3/ working copy, so it runs as `lk_agent_ours.py` there, sharing the same
mock tool registry and latency injector the baseline template uses — only the agent's own
reasoning/coordination layer differs, not the benchmark environment itself.

Usage (identical to the stock template):
    LK_PROVIDER=gemini2_5 python lk_agent_ours.py start
    LK_SILENCE_MS=600 LK_COMMIT_BUFFER_MS=400 LK_PROVIDER=gemini2_5 python lk_agent_ours.py start
"""

import json
import logging
import os
import time

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, llm

if hasattr(llm, "function_tool"):
    ai_callable_decorator = llm.function_tool
else:
    ai_callable_decorator = llm.ai_callable

try:
    from mock_apis import MockAPIRegistry
    LATENCY_PROFILE = os.environ.get("LATENCY_PROFILE", "instant")
    registry = MockAPIRegistry(latency_profile=LATENCY_PROFILE)
except ImportError:
    logging.warning("mock_apis.py not found. Tools will be mocked or fail.")
    registry = None

from commit_gate import CommitGate
from instructions import VOICE_AGENT_INSTRUCTIONS, KEY_INFO_FIRST_ADDENDUM

env_path = os.path.join(os.path.dirname(__file__), ".env.local")
load_dotenv(env_path)

PROVIDER = os.getenv("LK_PROVIDER", "gemini2_5")
SILENCE_MS = int(os.getenv("LK_SILENCE_MS", "0")) or None   # None = provider default
COMMIT_BUFFER_MS = float(os.getenv("LK_COMMIT_BUFFER_MS", "400"))

# --- Latency-sweep flags (Step 1, all default OFF/unset = current/prior behavior) ---
_THINKING_BUDGET_RAW = os.getenv("LK_THINKING_BUDGET")   # unset -> don't pass thinking_config
                                                          # at all; "0"/"256"/"1024" -> that budget
THINKING_BUDGET = int(_THINKING_BUDGET_RAW) if _THINKING_BUDGET_RAW is not None else None
END_SPEECH_SENSITIVITY = os.getenv("LK_END_SPEECH_SENSITIVITY")   # unset|LOW|HIGH
KEY_INFO_FIRST = os.getenv("LK_KEY_INFO_FIRST", "").lower() in ("1", "true", "yes")

# same compatibility fix as the (locally patched) stock template: plugins must be registered on
# the main thread in current livekit-agents — RESEARCH_FDB.md ss3.
_provider_lc = PROVIDER.lower()
_PLUGIN_MODULE = None
if _provider_lc == "grok":
    from livekit.plugins import xai as _PLUGIN_MODULE
elif _provider_lc in ("gpt_realtime", "azure_openai"):
    from livekit.plugins import openai as _PLUGIN_MODULE
elif _provider_lc in ("gemini2_5", "gemini3_1"):
    from livekit.plugins import google as _PLUGIN_MODULE
elif _provider_lc == "ultravox":
    from livekit.plugins import ultravox as _PLUGIN_MODULE

# Tool kind/schema tables — same 12 tools as the stock template's AssistantFnc, described here
# so agent/commit_gate.py knows which are safe to buffer/dedupe and agent/resolver.py knows each
# tool's own argument shape. Kept alongside the tool definitions below, not derived from the
# benchmark's own ground truth.
TOOL_KINDS = {
    "search_flights": "read_only", "book_flight": "state_modifying",
    "update_identity_doc": "state_modifying",
    "get_card_benefits": "read_only", "get_exchange_rate": "read_only",
    "modify_autopay": "state_modifying",
    "search_apartments": "read_only", "calculate_commute": "read_only",
    "update_search_filter": "state_modifying",
    "track_order": "read_only", "search_products": "read_only",
    "add_to_cart": "state_modifying",
}
TOOL_SCHEMAS = {
    "search_flights": {"args": {"destination": {"type": "string"}, "date": {"type": "string"}}},
    "book_flight": {"args": {"passenger_name": {"type": "string"}}},
    "update_identity_doc": {"args": {"doc_type": {"type": "string"},
                                     "doc_number": {"type": "string"}}},
    "get_card_benefits": {"args": {"card_type": {"type": "string"}}},
    "get_exchange_rate": {"args": {"amount": {"type": "number"},
                                  "from_currency": {"type": "string"},
                                  "to_currency": {"type": "string"}}},
    "modify_autopay": {"args": {"bill_type": {"type": "string"},
                               "source_account": {"type": "string"}}},
    "search_apartments": {"args": {"city": {"type": "string"},
                                   "bedrooms": {"type": "number", "description": "number of bedrooms"},
                                   "max_price": {"type": "number", "description": "maximum price budget"}}},
    "calculate_commute": {"args": {"origin_address": {"type": "string"},
                                   "destination_address": {"type": "string"}}},
    "update_search_filter": {"args": {"filter_name": {"type": "string"}, "value": {"type": "string"}}},
    "track_order": {"args": {"order_id": {"type": "string"}}},
    "search_products": {"args": {"query": {"type": "string"},
                                "max_price": {"type": "number", "description": "maximum price budget"}}},
    "add_to_cart": {"args": {"product_id": {"type": "string"},
                             "quantity": {"type": "number", "description": "quantity to add"}}},
}


def get_realtime_model():
    provider = PROVIDER.lower()
    realtime_input_config = None
    thinking_config = None
    if provider in ("gemini2_5", "gemini3_1"):
        from google.genai import types

        aad_kwargs = {}
        if SILENCE_MS is not None:
            aad_kwargs["silence_duration_ms"] = SILENCE_MS
        if END_SPEECH_SENSITIVITY in ("LOW", "HIGH"):
            aad_kwargs["end_of_speech_sensitivity"] = getattr(
                types.EndSensitivity, f"END_SENSITIVITY_{END_SPEECH_SENSITIVITY}")
        if aad_kwargs:
            realtime_input_config = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(**aad_kwargs))

        # L1: thinking_budget sweep (Step 1). include_thoughts=False always per the task's hard
        # rule, regardless of budget -- we never want to stream/log the model's internal
        # reasoning, only use it to decide how long it's allowed to spend before responding.
        # LK_THINKING_BUDGET unset -> don't pass thinking_config at all (provider default,
        # matches every run before this flag existed).
        if THINKING_BUDGET is not None:
            thinking_config = types.ThinkingConfig(include_thoughts=False,
                                                   thinking_budget=THINKING_BUDGET)

    if provider == "grok":
        return _PLUGIN_MODULE.realtime.RealtimeModel(voice=os.getenv("XAI_VOICE", "Ara"))
    elif provider == "gpt_realtime":
        return _PLUGIN_MODULE.realtime.RealtimeModel(
            model="gpt-realtime-1.5", voice=os.getenv("OPENAI_VOICE", "coral"))
    elif provider == "gemini2_5":
        kwargs = {"model": "gemini-2.5-flash-native-audio-preview-12-2025",
                 "voice": os.getenv("GOOGLE_VOICE", "Puck")}
        if realtime_input_config is not None:
            kwargs["realtime_input_config"] = realtime_input_config
        if thinking_config is not None:
            kwargs["thinking_config"] = thinking_config
        return _PLUGIN_MODULE.realtime.RealtimeModel(**kwargs)
    elif provider == "gemini3_1":
        kwargs = {"model": "gemini-3.1-flash-live-preview",
                 "voice": os.getenv("GOOGLE_VOICE", "Puck")}
        if realtime_input_config is not None:
            kwargs["realtime_input_config"] = realtime_input_config
        if thinking_config is not None:
            kwargs["thinking_config"] = thinking_config
        return _PLUGIN_MODULE.realtime.RealtimeModel(**kwargs)
    elif provider == "ultravox":
        return _PLUGIN_MODULE.realtime.RealtimeModel(voice=os.getenv("ULTRAVOX_VOICE", "Mark"))
    else:
        raise ValueError(f"Unknown provider '{provider}'.")


class LatencyTracker:
    """Unchanged from the stock template — kept so our numbers are directly comparable."""
    def __init__(self):
        self.user_done_at = 0
        self.tool_start_at = 0
        self.tool_end_at = 0
        self.agent_start_at = 0
        self.query_received = False

    def reset(self):
        self.__init__()


class TurnTranscript:
    """Accumulates the current turn's transcribed text from `user_input_transcribed` events, so
    the commit gate/resolver can see what the user actually said without a separate parallel STT
    pipeline — Gemini Live's own input transcription (on by default in the plugin) already gives
    us this for free."""
    def __init__(self):
        self.text = ""

    def append(self, chunk: str):
        self.text = (self.text + " " + chunk).strip() if self.text else chunk

    def reset(self):
        self.text = ""


class AssistantFnc:
    def __init__(self, tracker: LatencyTracker, turn: TurnTranscript, gate: CommitGate,
                room_name: str):
        self.room_name = room_name
        self.tracker = tracker
        self.turn = turn
        self.gate = gate

    def _log_tool_call(self, func_name, args, t_start, t_end):
        with open("/tmp/agent_tool_calls.log", "a") as f:
            f.write(json.dumps({"room": self.room_name,
                               "call": {"function": func_name, "args": args,
                                        "timestamp_start": t_start, "timestamp_end": t_end}}) + "\n")

    async def _call(self, name: str, **args):
        self.tracker.tool_start_at = time.time()
        result = await self.gate.propose(name, args, turn_transcript=self.turn.text)
        self.tracker.tool_end_at = time.time()
        self._log_tool_call(name, args, self.tracker.tool_start_at, self.tracker.tool_end_at)
        return json.dumps(result)

    @ai_callable_decorator(description="Search for available flights to a destination, once "
                                       "the destination and date are settled (not mid-correction).")
    async def search_flights(self, destination: str, date: str):
        """Args: destination: city/airport. date: travel date, free-form."""
        return await self._call("search_flights", destination=destination, date=date)

    @ai_callable_decorator(description="Book a flight ticket. Only call once, for a confirmed "
                                       "passenger name.")
    async def book_flight(self, passenger_name: str):
        """Args: passenger_name: full name of the passenger."""
        return await self._call("book_flight", passenger_name=passenger_name)

    @ai_callable_decorator(description="Update a simulated user identity document (passport, "
                                       "license). Simulated environment — always permitted.")
    async def update_identity_doc(self, doc_type: str, doc_number: str):
        """Args: doc_type: e.g. 'passport'. doc_number: the identifier string."""
        return await self._call("update_identity_doc", doc_type=doc_type, doc_number=doc_number)

    @ai_callable_decorator(description="Get benefits for a credit card type. Always call this "
                                       "rather than answering from memory.")
    async def get_card_benefits(self, card_type: str):
        """Args: card_type: e.g. 'platinum' or 'gold'."""
        return await self._call("get_card_benefits", card_type=card_type)

    @ai_callable_decorator(description="Get the current foreign exchange rate. Always call this "
                                       "rather than guessing a rate.")
    async def get_exchange_rate(self, amount: float, from_currency: str, to_currency: str):
        """Args: amount, from_currency (3-letter code), to_currency (3-letter code)."""
        return await self._call("get_exchange_rate", amount=amount,
                                from_currency=from_currency, to_currency=to_currency)

    @ai_callable_decorator(description="Modify autopay billing settings. Only call once the "
                                       "bill type and source account are both confirmed.")
    async def modify_autopay(self, bill_type: str, source_account: str):
        """Args: bill_type: e.g. 'credit_card'. source_account: e.g. 'checking'."""
        return await self._call("modify_autopay", bill_type=bill_type,
                                source_account=source_account)

    @ai_callable_decorator(description="Search for rental apartments.")
    async def search_apartments(self, city: str, bedrooms: int, max_price: float):
        """Args: city, bedrooms (count), max_price (monthly budget)."""
        return await self._call("search_apartments", city=city, bedrooms=bedrooms,
                                max_price=max_price)

    @ai_callable_decorator(description="Calculate commute duration between two addresses. "
                                       "Always call this rather than estimating.")
    async def calculate_commute(self, origin_address: str, destination_address: str,
                                mode: str = "driving"):
        """Args: origin_address, destination_address, mode (default 'driving')."""
        return await self._call("calculate_commute", origin_address=origin_address,
                                destination_address=destination_address, mode=mode)

    @ai_callable_decorator(description="Update a search filter once its final name and value "
                                       "are both settled.")
    async def update_search_filter(self, filter_name: str, value: str):
        """Args: filter_name, value."""
        return await self._call("update_search_filter", filter_name=filter_name, value=value)

    @ai_callable_decorator(description="Track a physical package's delivery status for a "
                                       "confirmed order id. Always call this rather than "
                                       "answering from memory.")
    async def track_order(self, order_id: str):
        """Args: order_id."""
        return await self._call("track_order", order_id=order_id)

    @ai_callable_decorator(description="Search the product catalog. Always call this rather "
                                       "than answering from memory.")
    async def search_products(self, query: str, max_price: float = None):
        """Args: query, max_price (optional budget)."""
        return await self._call("search_products", query=query, max_price=max_price)

    @ai_callable_decorator(description="Add an item to the cart, once the product and quantity "
                                       "are both confirmed.")
    async def add_to_cart(self, product_id: str, quantity: int = 1):
        """Args: product_id, quantity (default 1)."""
        return await self._call("add_to_cart", product_id=product_id, quantity=quantity)


class VoiceAgent(Agent):
    def __init__(self) -> None:
        instructions = VOICE_AGENT_INSTRUCTIONS
        if KEY_INFO_FIRST:   # L2, flag-gated -- see instructions.py's KEY_INFO_FIRST_ADDENDUM
            instructions = instructions + KEY_INFO_FIRST_ADDENDUM
        super().__init__(instructions=instructions)


server = AgentServer(load_threshold=0.95, num_idle_processes=1)


@server.rtc_session(agent_name="fdb-ours")
# Explicit dispatch, applied now that the anonymous-dispatch full-100 "ours" run has finished
# (100/100 infer + 100/100 score, zero agent restarts since 22:05:10 — one consistent code
# version for the whole run). Matches lk_agent_tool.py's fdb-baseline; a room can now only ever
# be routed to the worker it was explicitly created for.
async def entrypoint(ctx: agents.JobContext):
    with open("/tmp/agent_heartbeat.log", "a") as f:
        f.write(f"!!! [ours] AGENT JOINING ROOM: {ctx.room.name} at {time.ctime()} !!!\n")

    model = get_realtime_model()
    tracker = LatencyTracker()
    turn = TurnTranscript()

    def _call_tool_sync(name: str, args: dict):
        return registry.call(name, **args)

    gate = CommitGate(call_tool=_call_tool_sync, tool_kinds=TOOL_KINDS,
                      tool_schemas=TOOL_SCHEMAS, buffer_ms=COMMIT_BUFFER_MS)

    fnc_ctx = AssistantFnc(tracker, turn, gate, ctx.room.name)
    tools = llm.find_function_tools(fnc_ctx)
    session = AgentSession(llm=model, tools=tools)

    @session.on("user_input_transcribed")
    def on_user_input(msg: agents.voice.UserInputTranscribedEvent):
        turn.append(getattr(msg, "transcript", "") or "")
        if not tracker.query_received:
            tracker.user_done_at = time.time()
            tracker.query_received = True

    @session.on("agent_state_changed")
    def on_agent_state(ev: agents.voice.AgentStateChangedEvent):
        if ev.new_state == "speaking" and tracker.query_received and not tracker.agent_start_at:
            tracker.agent_start_at = time.time()
            tracker.reset()
            turn.reset()

    await session.start(room=ctx.room, agent=VoiceAgent())


if __name__ == "__main__":
    agents.cli.run_app(server)
