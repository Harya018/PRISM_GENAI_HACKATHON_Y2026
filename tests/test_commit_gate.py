import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.commit_gate import CommitGate

FLIGHT_SCHEMA = {"args": {"destination": {"type": "string"}, "date": {"type": "string"}}}


def _gate(buffer_ms=50, calls_log=None):
    calls_log = calls_log if calls_log is not None else []

    def call_tool(name, args):
        calls_log.append((name, dict(args)))
        return {"status": "success", "echo": args}

    return CommitGate(
        call_tool=call_tool,
        tool_kinds={"search_flights": "read_only", "book_flight": "state_modifying"},
        tool_schemas={"search_flights": FLIGHT_SCHEMA},
        buffer_ms=buffer_ms,
    ), calls_log


def _run(coro):
    return asyncio.run(coro)


def test_single_call_executes_once():
    gate, calls = _gate()
    result = _run(gate.propose("search_flights", {"destination": "Denver"}))
    assert calls == [("search_flights", {"destination": "Denver"})]
    assert result["echo"]["destination"] == "Denver"


def test_self_correction_supersedes_stale_call():
    """The core commit-gate behavior: a same-tool call proposed shortly after an earlier one
    (before its buffer elapses) must be the ONLY one that actually executes — the earlier
    (pre-correction) call must never reach call_tool at all."""
    gate, calls = _gate(buffer_ms=200)

    async def scenario():
        first = asyncio.create_task(gate.propose("search_flights", {"destination": "Paris"}))
        await asyncio.sleep(0.05)  # well within the 200ms buffer
        second = asyncio.create_task(gate.propose("search_flights", {"destination": "Berlin"}))
        r1 = await first
        r2 = await second
        return r1, r2

    r1, r2 = _run(scenario())
    assert calls == [("search_flights", {"destination": "Berlin"})], (
        "the stale Paris call must never reach the real tool")
    # both invocations resolve to the same (real, corrected) result — no hang, no duplicate exec
    assert r1["echo"]["destination"] == "Berlin"
    assert r2["echo"]["destination"] == "Berlin"


def test_call_outside_buffer_window_executes_independently():
    gate, calls = _gate(buffer_ms=50)

    async def scenario():
        r1 = await gate.propose("search_flights", {"destination": "Denver"})
        r2 = await gate.propose("search_flights", {"destination": "Seattle"})
        return r1, r2

    _run(scenario())
    assert calls == [
        ("search_flights", {"destination": "Denver"}),
        ("search_flights", {"destination": "Seattle"}),
    ], "two calls separated by more than the buffer window are independent, not a correction"


def test_duplicate_state_modifying_call_is_blocked_not_reexecuted():
    gate, calls = _gate(buffer_ms=10)

    async def scenario():
        r1 = await gate.propose("book_flight", {"flight_id": "FL1", "passenger_name": "Alice"})
        r2 = await gate.propose("book_flight", {"flight_id": "FL1", "passenger_name": "Alice"})
        return r1, r2

    r1, r2 = _run(scenario())
    assert len(calls) == 1, "an identical state-modifying call must execute at most once"
    assert r1 == r2


def test_different_args_state_modifying_call_is_not_blocked():
    gate, calls = _gate(buffer_ms=10)

    async def scenario():
        await gate.propose("book_flight", {"flight_id": "FL1", "passenger_name": "Alice"})
        await gate.propose("book_flight", {"flight_id": "FL2", "passenger_name": "Bob"})

    _run(scenario())
    assert len(calls) == 2


def test_resolver_corrects_stale_argument_before_execution():
    """Even if the model's own function-call arguments still carry the pre-correction value,
    a parallel transcript run through the resolver overrides it before the real call fires."""
    gate, calls = _gate(buffer_ms=10)
    transcript = "Search flights to Paris — actually, no, Berlin instead."
    result = _run(gate.propose("search_flights", {"destination": "Paris"},
                               turn_transcript=transcript))
    assert calls == [("search_flights", {"destination": "Berlin"})]
    assert result["echo"]["destination"] == "Berlin"
    correction_events = [e for e in gate.log if e["event"] == "resolver_corrected_arg"]
    assert correction_events and correction_events[0]["to_value"] == "Berlin"


def test_resolver_correction_on_numeric_arg_is_type_coerced():
    """The regression this guards against: a resolved correction for a numeric arg must reach
    the real tool as a number, never a string — a string silently corrupting a numeric arg
    caused a real TypeError in mock_apis.py (`max_price - 100` on a str) during the FDB-v3
    rollback-subset run, traced back to the resolver's own candidate values always being str."""
    apt_schema = {"args": {"city": {"type": "string"},
                          "max_price": {"type": "number", "description": "maximum price budget"}}}
    calls_log = []

    def call_tool(name, args):
        calls_log.append((name, dict(args)))
        return {"status": "success"}

    gate = CommitGate(call_tool=call_tool,
                      tool_kinds={"search_apartments": "read_only"},
                      tool_schemas={"search_apartments": apt_schema},
                      buffer_ms=10)
    # Block-only: the override only fires when the model's own arg equals a value this turn
    # actually stated and then corrected away (500 -> 2000 here), not merely "differs from
    # whatever the resolver would compute" — so max_price=500 must genuinely appear pre-correction.
    transcript = ("I'm interested in Boston with a budget of five hundred — wait, actually, "
                 "Chicago instead, keep the max price around two thousand dollars a month.")
    _run(gate.propose("search_apartments", {"city": "Boston", "max_price": 500},
                      turn_transcript=transcript))
    assert calls_log == [("search_apartments", {"city": "Chicago", "max_price": 2000})]
    assert isinstance(calls_log[0][1]["max_price"], int), (
        "a corrected numeric arg must reach the tool as a number, not the resolver's raw str")


def test_resolver_never_overrides_an_argument_with_no_correction_on_record():
    """Block-only guarantee, tested directly: an argument the model already got right must be
    left alone even when the transcript contains OTHER numbers that a resolver parsing quirk
    could misread — because nothing was ever stated-then-corrected-away for THIS arg, there is
    no stale value to match against, so no override can fire. This is what makes the resolver
    safe to ship even though its own number/proper-noun heuristics aren't perfect: it can only
    ever correct a value that's provably stale, never silently replace one that was already
    correct with its own guess."""
    apt_schema = {"args": {"city": {"type": "string"},
                          "bedrooms": {"type": "number", "description": "number of bedrooms"}}}
    calls_log = []

    def call_tool(name, args):
        calls_log.append((name, dict(args)))
        return {"status": "success"}

    gate = CommitGate(call_tool=call_tool,
                      tool_kinds={"search_apartments": "read_only"},
                      tool_schemas={"search_apartments": apt_schema},
                      buffer_ms=10)
    # "bedrooms" is never corrected in this transcript at all — only city is. The model's own
    # bedrooms=3 must survive untouched regardless of any other number mentioned in passing.
    transcript = "Search in Denver — actually, Miami instead, three bedrooms, near 5th avenue."
    _run(gate.propose("search_apartments", {"city": "Denver", "bedrooms": 3},
                      turn_transcript=transcript))
    assert calls_log == [("search_apartments", {"city": "Miami", "bedrooms": 3})]


def test_coerce_to_arg_type_rejects_non_numeric_string():
    """Direct unit test of the safety guard: if a resolved value for a declared-number arg can't
    be parsed as a number at all, the gate must refuse the override rather than pass a bad
    string through to a tool that does arithmetic on it."""
    gate, _ = _gate()
    gate.tool_schemas["search_flights"]["args"]["seat_count"] = {"type": "number"}
    assert gate._coerce_to_arg_type("search_flights", "seat_count", "2000") == 2000
    assert gate._coerce_to_arg_type("search_flights", "seat_count", "not-a-number") is None


def test_on_committed_fires_once_right_before_real_execution():
    """Additive hook for the extension's instant spoken acknowledgement: must fire exactly once,
    after a call has survived the buffer (never for a superseded/stale one), and strictly before
    call_tool runs — the one moment safe to say "checking that" without risking a premature
    utterance the model then has to walk back."""
    committed = []
    executed = []

    def call_tool(name, args):
        executed.append((name, dict(args)))
        return {"status": "success"}

    def on_committed(name, args):
        committed.append((name, dict(args)))

    gate = CommitGate(call_tool=call_tool,
                      tool_kinds={"search_flights": "read_only"},
                      tool_schemas={"search_flights": FLIGHT_SCHEMA},
                      buffer_ms=200, on_committed=on_committed)

    async def scenario():
        first = asyncio.create_task(gate.propose("search_flights", {"destination": "Paris"}))
        await asyncio.sleep(0.05)  # well within the 200ms buffer — Paris gets superseded
        second = asyncio.create_task(gate.propose("search_flights", {"destination": "Berlin"}))
        await first
        await second

    _run(scenario())
    assert committed == [("search_flights", {"destination": "Berlin"})], (
        "on_committed must fire once, only for the winning call, never the superseded one")
    assert executed == committed


def test_on_event_receives_every_log_entry():
    """Additive hook for streaming the commit gate's own transparency log to a UI (frontend
    work, tomorrow) — must see exactly what self.log records, in order."""
    seen = []
    gate, _ = _gate(buffer_ms=10)
    gate.on_event = lambda event, entry: seen.append(event)
    _run(gate.propose("search_flights", {"destination": "Denver"}))
    assert seen == ["buffered", "executed"] == [e["event"] for e in gate.log]


def test_confirm_required_tool_refuses_without_an_affirmative_reply():
    """CODE-enforced, not prompt-trusted: a risky tool named in confirm_required must never
    reach call_tool on a first mention, even if the model tries — only a transcript containing
    a plain affirmative unlocks it, and only for the rest of that session."""
    calls_log = []

    def call_tool(name, args):
        calls_log.append((name, dict(args)))
        return {"status": "success"}

    gate = CommitGate(call_tool=call_tool,
                      tool_kinds={"reset_network_settings": "state_modifying"},
                      tool_schemas={"reset_network_settings": {"args": {}}},
                      buffer_ms=10,
                      confirm_required=frozenset({"reset_network_settings"}))

    result = _run(gate.propose("reset_network_settings", {},
                               turn_transcript="Please reset my network settings."))
    assert calls_log == [], "must not execute without an affirmative reply on record"
    assert result["status"] == "confirmation_required"

    result2 = _run(gate.propose("reset_network_settings", {}, turn_transcript="Yes, go ahead."))
    assert calls_log == [("reset_network_settings", {})]
    assert result2["status"] == "success"


def test_log_is_transparent_and_ordered():
    gate, _ = _gate(buffer_ms=10)
    _run(gate.propose("search_flights", {"destination": "Denver"}))
    kinds = [e["event"] for e in gate.log]
    assert kinds == ["buffered", "executed"]
