"""Commit gate — buffers proposed tool calls for a short debounce window before they actually
execute, so a same-tool follow-up call from a self-correction ("Paris... actually Berlin") can
supersede an earlier one before it ever runs — the earlier call is never recorded as an "extra
call" at all, which is what FDB-v3's strict pass-rate check requires (RESEARCH_FDB.md ss1: any
extra call fails the scenario, read-only or not).

Also owns three other safety properties the theme architecture asks for:
  - idempotency: an identical state-modifying call (same tool + same normalized args) already
    executed this session is refused and its original result is returned instead — zero
    duplicate state-changing calls, provably from the log this class keeps.
  - correction-aware re-validation, BLOCK-ONLY: if a parallel transcript + the tool's own schema
    are available (agent/resolver.py), an argument is replaced ONLY when its value exactly
    equals something this turn explicitly corrected away for that same arg (`stale_value_map`);
    the resolver never injects a freshly-computed value the model didn't itself propose. An
    already-correct argument is therefore never at risk from a resolver parsing bug — there is
    nothing to block it against.
  - confirmation-before-risky-action, CODE-enforced not just prompt-trusted: a tool named in
    `confirm_required` is refused (never reaches `call_tool` at all) unless the proposing turn's
    own transcript contains a plain affirmative ("yes", "go ahead", "do it", ...). This doesn't
    rely on the model actually asking first — even if it tries to call a risky tool on the very
    first mention, the call itself cannot execute without an affirmative reply on record, the
    same way the resolver's block-only design refuses to trust a value it can't verify.

Every decision (buffered, superseded/blocked, deduped, executed) is appended to `self.log` —
the "transparent pipeline" differentiator: nothing happens here that isn't inspectable
afterward, by a human, a results table, or (later) the frontend's commit-gate panel.

Buffer window is configurable (`buffer_ms`) precisely so it can be swept empirically (A2) —
this is a deliberate latency-vs-safety knob, not a fixed constant, and should only ship at a
value proven (on real runs, not assumption) to help pass rate more than it costs latency.

H1 additions (argument redaction, an async-only tool timeout, and per-tool admission control)
were pulled in after reviewing another team's write-up of their own interruptible-agent
architecture, which described a harness with admission control, timeouts, and redaction as a
single bullet. All three are additive and default OFF/unbounded (current behavior unchanged
unless explicitly configured) except redaction, which only ever touches what's logged/streamed,
never what reaches call_tool.

One idea from that same write-up was deliberately NOT adopted here: starting a read-only tool
call speculatively, in the background, the instant it's proposed -- before the buffer window
confirms it won't be superseded -- to overlap the tool's own latency with the correction-
detection buffer. Investigated and rejected: FDB-v3's own mock_apis.py (`MockAPIRegistry.call`)
logs every invocation into a `CallLogger` the instant `call_tool` runs, and that log is what the
benchmark reads back as `actual_tool_calls` for scoring. Starting a call speculatively means
invoking call_tool before we know it will be superseded -- which would get the superseded call
permanently recorded as an "extra call" and penalized, exactly the failure mode this whole class
exists to prevent. This is safe for a pure information-retrieval system with no external logging
tied to invocation, which is what that other project's own architecture is; it is not safe here,
where the very act of calling is what gets scored.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from .resolver import resolve_turn   # imported as part of the agent/ package (tests)
except ImportError:
    from resolver import resolve_turn    # imported standalone (deployed flat into v3/)


def _norm_args(args: Dict[str, Any]) -> str:
    return json.dumps({k: str(v).strip().lower() for k, v in sorted(args.items())},
                      sort_keys=True)


# H1: argument redaction for anything logged/streamed (self.log, on_event) -- never applied to
# what actually reaches call_tool, only to what a human/UI/log file sees. Name-pattern based
# (not a fixed per-tool list) so it also covers the extension's own tools without maintenance;
# matches agent/lk_agent.py's real sensitive args (update_identity_doc's doc_number,
# modify_autopay's source_account) by construction, not by hardcoding those two names.
_SENSITIVE_ARG_RE = re.compile(
    r"(doc_number|account|routing|ssn|password|passwd|card_number|cvv|\bpin\b|secret|"
    r"api_key|token)", re.I)


def _redact(args: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("***REDACTED***" if _SENSITIVE_ARG_RE.search(k) else v)
           for k, v in args.items()}


def _redact_value(arg_name: str, value: Any) -> Any:
    return "***REDACTED***" if _SENSITIVE_ARG_RE.search(arg_name) else value


_CONFIRMATION_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|go ahead|do it|please do|confirm(?:ed)?|that's right|correct|"
    r"proceed|sounds good)\b", re.I)


@dataclass
class _Pending:
    call_id: str
    args: Dict[str, Any]
    future: "asyncio.Future"


@dataclass
class CommitGate:
    call_tool: Callable[[str, Dict[str, Any]], Any]   # sync or async; the real mock_apis call
    tool_kinds: Dict[str, str]                          # tool_name -> "read_only"|"state_modifying"
    tool_schemas: Dict[str, Dict[str, Any]]             # tool_name -> schema, for the resolver
    buffer_ms: float = 400.0
    confirm_required: frozenset = field(default_factory=frozenset)  # tool names needing an
                                                                    # affirmative reply on record
    log: List[Dict[str, Any]] = field(default_factory=list)
    on_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None   # additive/optional: fired
                                                                      # alongside every _emit,
                                                                      # for latency streaming to
                                                                      # a UI (extension only —
                                                                      # the benchmark agent never
                                                                      # passes this, so its own
                                                                      # behavior is unaffected)
    on_committed: Optional[Callable[[str, Dict[str, Any]], Any]] = None   # additive/optional:
                                                                          # fired once a call has
                                                                          # survived the buffer
                                                                          # and is about to
                                                                          # execute for real —
                                                                          # the one safe moment
                                                                          # for the extension to
                                                                          # speak an instant
                                                                          # "checking that" ack
                                                                          # without risking a
                                                                          # premature/superseded
                                                                          # utterance
    normalize_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None   # A1,
                                                                          # additive/optional:
                                                                          # agent/normalize.py's
                                                                          # normalize_args, only
                                                                          # wired in when
                                                                          # LK_NORMALIZE_ARGS=1
                                                                          # (agent/lk_agent.py) —
                                                                          # applied AFTER the
                                                                          # resolver, not before:
                                                                          # the resolver matches
                                                                          # stale values by their
                                                                          # raw string form (see
                                                                          # _apply_resolver), so
                                                                          # normalizing types
                                                                          # first would break
                                                                          # that string match
    dedupe_read_only: bool = False   # P1, default off: when True, an identical (tool,
                                     # normalized args) READ-ONLY call is also served from the
                                     # dedupe cache instead of re-executing — off by default
                                     # because read-only re-calls were never penalized before
                                     # this flag existed and this changes real behavior
    tool_timeout_s: Optional[float] = None   # H1, default off (unbounded, current behavior):
                                             # only meaningful for an ASYNC call_tool (a sync one
                                             # can't be interrupted mid-call without running it in
                                             # an executor, which none of this codebase's mock
                                             # tools need — all are fast, in-memory, deterministic)
    max_calls_per_tool: Optional[int] = None   # H1, default off (unbounded, current behavior):
                                               # admission control against a runaway loop of
                                               # genuinely-different-argument calls to the same
                                               # tool — distinct from dedupe, which only blocks an
                                               # EXACT repeat. Counts only calls that actually
                                               # reach call_tool (committed, not superseded), so a
                                               # long correction chain on one tool never trips it
    _pending: Dict[str, _Pending] = field(default_factory=dict)
    _executed: Dict[str, Any] = field(default_factory=dict)   # dedupe key -> result
    _confirmed: set = field(default_factory=set)   # confirm_required tools cleared this session
    _call_counts: Dict[str, int] = field(default_factory=dict)   # tool_name -> committed count
    _call_seq: int = 0

    def _next_call_id(self) -> str:
        self._call_seq += 1
        return f"c{self._call_seq}"

    def _emit(self, event: str, **fields: Any) -> None:
        entry = {"event": event, "t": time.monotonic(), **fields}
        self.log.append(entry)
        if self.on_event is not None:
            self.on_event(event, entry)

    def _coerce_to_arg_type(self, tool_name: str, arg_name: str,
                            value: str) -> Optional[Any]:
        """The resolver always returns strings (agent/resolver.py's `SlotCandidate.value`); a
        numeric arg must never be silently replaced by one, even if it happens to look
        numeric — the schema's declared type is the source of truth. Returns None (never
        override) when the value can't be coerced to the arg's declared type, e.g. a
        resolver mis-parse the fast path didn't catch, rather than letting a str reach a
        tool that does arithmetic on it (confirmed failure mode: TypeError on `max_price -
        100` when a corrected arg came through as "2" instead of 2000)."""
        spec = (self.tool_schemas.get(tool_name, {}).get("args") or {}).get(arg_name, {})
        if spec.get("type") == "number":
            try:
                f = float(value)
            except (TypeError, ValueError):
                return None
            return int(f) if f.is_integer() else f
        return value

    def _apply_resolver(self, tool_name: str, args: Dict[str, Any],
                        turn_transcript: Optional[str]) -> Dict[str, Any]:
        """BLOCK-ONLY: replace an argument only when its value exactly equals something this
        turn explicitly corrected away for that same arg. Never inject the resolver's own
        computed "final" value for an arg that wasn't actually superseded — an arg the model
        already got right, with no correction on record against it, is left untouched no matter
        what the resolver's own (possibly buggy) parse of the sentence would compute."""
        schema = self.tool_schemas.get(tool_name)
        if not turn_transcript or not schema:
            return args
        resolved = resolve_turn(turn_transcript, schema)
        stale_map = resolved.stale_value_map()
        out = dict(args)
        for k, v in out.items():
            per_arg_stale = stale_map.get(k)
            if not per_arg_stale:
                continue
            stale_hit = per_arg_stale.get(str(v).strip().lower())
            if stale_hit is None:
                continue
            coerced = self._coerce_to_arg_type(tool_name, k, stale_hit)
            if coerced is None:
                self._emit("resolver_correction_rejected", tool=tool_name, arg=k,
                          raw_value=_redact_value(k, stale_hit), reason="failed_type_coercion")
                continue
            self._emit("resolver_corrected_arg", tool=tool_name, arg=k,
                      from_value=_redact_value(k, out[k]), to_value=_redact_value(k, coerced))
            out[k] = coerced
        return out

    async def propose(self, tool_name: str, args: Dict[str, Any],
                      turn_transcript: Optional[str] = None) -> Any:
        """Buffer a proposed call; returns the eventual real result (this call's own, or the
        result of whichever later call superseded it)."""
        kind = self.tool_kinds.get(tool_name, "read_only")
        args = self._apply_resolver(tool_name, args, turn_transcript)
        if self.normalize_fn is not None:   # A1, off unless LK_NORMALIZE_ARGS=1
            args = self.normalize_fn(tool_name, args)

        if tool_name in self.confirm_required and tool_name not in self._confirmed:
            if turn_transcript and _CONFIRMATION_RE.search(turn_transcript):
                self._confirmed.add(tool_name)
                self._emit("confirmed", tool=tool_name)
            else:
                self._emit("confirmation_required", tool=tool_name, args=_redact(args))
                return {"status": "confirmation_required",
                       "message": f"{tool_name} requires the user's explicit confirmation "
                                  "before it can run."}

        if kind == "state_modifying" or (kind == "read_only" and self.dedupe_read_only):
            dedupe_key = tool_name + "|" + _norm_args(args)
            if dedupe_key in self._executed:
                self._emit("blocked_duplicate", tool=tool_name, args=_redact(args), kind=kind)
                return self._executed[dedupe_key]

        call_id = self._next_call_id()
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()

        prior = self._pending.get(tool_name)
        if prior is not None and not prior.future.done():
            self._emit("superseded", tool=tool_name, old_args=_redact(prior.args),
                      old_call_id=prior.call_id, new_call_id=call_id)

        self._pending[tool_name] = _Pending(call_id, args, fut)
        self._emit("buffered", tool=tool_name, args=_redact(args), call_id=call_id,
                  buffer_ms=self.buffer_ms)

        # P2: a realtime model can retract a tool call it already proposed (observed in
        # production logs as the provider's own "server cancelled tool calls" event, which
        # LiveKit surfaces to us as this coroutine's task being cancelled). Before this fix,
        # a CancelledError here propagated straight out of propose() with no cleanup: the
        # _pending entry for this call_id was left behind with its future never resolved, so a
        # later call to the same tool would see it as "not done" and wrongly log a "superseded"
        # event for a call that was actually dropped by cancellation, and anything awaiting
        # that dangling future directly would hang forever.
        committed = False
        try:
            await asyncio.sleep(self.buffer_ms / 1000.0)

            current = self._pending.get(tool_name)
            if current is None or current.call_id != call_id:
                # a later call superseded us before the buffer elapsed — wait for its result
                # instead of ever executing the stale one.
                return await current.future

            # H1: admission control -- a genuinely different-argument call to the same tool,
            # repeated past a configured ceiling, is refused the same way a duplicate is (never
            # reaches call_tool). Counts only committed calls, so a long same-tool correction
            # chain (Paris -> Berlin -> ... -> Tokyo, all superseding each other) never trips
            # this on its own -- only actually-executed calls count toward the budget.
            if (self.max_calls_per_tool is not None
                    and self._call_counts.get(tool_name, 0) >= self.max_calls_per_tool):
                self._emit("call_budget_exceeded", tool=tool_name, args=_redact(current.args),
                          limit=self.max_calls_per_tool)
                budget_result = {"status": "call_budget_exceeded",
                                 "message": f"{tool_name} has already been called "
                                            f"{self.max_calls_per_tool} times this session."}
                if not fut.done():
                    fut.set_result(budget_result)
                return budget_result

            # we're the winner: about to execute for real, exactly once. This is the one safe
            # point to fire an instant spoken acknowledgement (extension only) — anything before
            # this line could still be superseded by a correction, or now, cancelled.
            committed = True
            self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
            if self.on_committed is not None:
                maybe_coro = self.on_committed(tool_name, current.args)
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro

            result = self.call_tool(tool_name, current.args)
            if asyncio.iscoroutine(result):
                # H1: a timeout is only meaningful here -- a sync call_tool (every tool in this
                # codebase today) can't be interrupted mid-call without an executor, which none
                # of these fast, in-memory mocks need.
                if self.tool_timeout_s is not None:
                    result = await asyncio.wait_for(result, timeout=self.tool_timeout_s)
                else:
                    result = await result
        except asyncio.CancelledError:
            if committed:
                # too late to drop -- call_tool may already have run (or is synchronously
                # certain to have completed, for the sync call_tool this codebase always uses).
                # Can't be undone; log and count it rather than pretend it didn't happen.
                self._emit("cancelled_after_committed", tool=tool_name, call_id=call_id)
            else:
                # dropped cleanly before execution -- the normal, expected outcome of a
                # server-side cancellation racing our buffer window.
                still_pending = self._pending.get(tool_name)
                if still_pending is not None and still_pending.call_id == call_id:
                    del self._pending[tool_name]
                self._emit("cancelled", tool=tool_name, call_id=call_id)
            if not fut.done():
                fut.cancel()
            raise
        except Exception as e:
            # H1 bug fix, not just a new feature: this branch didn't exist before -- a real
            # exception from call_tool (a bad mock-API arg, or now, a tool_timeout_s expiring)
            # used to propagate straight out of propose() with fut left permanently incomplete.
            # That's fine for THIS call's own awaiter (the exception reaches it directly), but
            # anyone who was superseded and is instead awaiting THIS future (the "wait for
            # whichever call wins" path, a few lines up) would hang forever, since nothing ever
            # completed it. Setting the exception here lets that waiter re-raise the same error
            # instead of hanging.
            self._emit("tool_timeout" if isinstance(e, asyncio.TimeoutError) else "call_failed",
                      tool=tool_name, call_id=call_id, error=repr(e))
            if not fut.done():
                fut.set_exception(e)
            raise

        if kind == "state_modifying" or (kind == "read_only" and self.dedupe_read_only):
            self._executed[tool_name + "|" + _norm_args(current.args)] = result
        self._emit("executed", tool=tool_name, args=_redact(current.args), result=result,
                  call_id=call_id)
        if not fut.done():
            fut.set_result(result)
        return result
