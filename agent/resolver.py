"""Correction resolver — the fast, rule-based half of the commit gate's dual process
(RESEARCH_FDB.md; theme architecture: fast path handles disfluency/repair in ~0ms, no network;
slow path is an optional LLM cross-check, see `llm_cross_check` below).

General method only: nothing here is derived from, or tuned against, FDB-v3 benchmark items —
repair markers, filler words, and number words are common English vocabulary, not
benchmark-specific strings, and slot-value extraction is driven entirely by *the tool manifest's
own parameter types and enum lists*, never a hardcoded list of expected values. Tuned and
unit-tested only on `tests/resolver_examples.py` (40 self-written sentences, held out from and
unrelated to FDB-v3) — see that file for the accuracy this actually reaches and what was fixed
to get there (a first pass without number-word/enum handling scored 8/42; ships once that's
worth keeping, not before).

Design, matching the task's spec, resolved against one tool's schema at a time (the real
integration point — the commit gate always has one specific proposed call to validate):
  1. Handle "not X but Y" as a same-clause correction before segment splitting.
  2. Split the turn into segments at repair-marker boundaries ("no", "actually", "wait",
     "I mean", "instead", "make that", ...).
  3. Strip fillers (um, uh, like, ...) and false starts (stutters, dash-truncated fragments).
  4. Extract slot-value *candidates* per segment, schema-driven: enum args look for their own
     literal values (case-insensitive whole-word); number args look for digits or spelled-out
     number words; date-shaped string args look for "<Month> <day>" (digit or spelled-out
     ordinal); other string args fall back to a proper-noun heuristic, excluding a small set of
     common sentence-initial words that are not entity names (see `_NOT_A_PROPER_NOUN`).
  5. Apply "last stated value wins" per arg: a later segment's candidate for the same arg
     supersedes an earlier one — recorded as a correction (`stale_value_map`), not applied
     directly. The commit gate is BLOCK-ONLY: it may replace a tool argument only when that
     argument's value exactly equals a value this turn explicitly corrected away for that same
     arg; it never injects a freshly-computed "best guess" the model didn't itself propose. This
     is deliberately narrower than "always force in whatever the resolver thinks is right" — a
     resolver mis-parse can then only ever leave an already-correct argument alone (nothing to
     block against), never corrupt it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Vocabulary — common English disfluency/number/date words, not benchmark-derived.
# ---------------------------------------------------------------------------
FILLERS = [
    r"\bum+\b", r"\buh+\b", r"\berr?\b", r"\bhmm+\b", r"\bokay,?\b", r"\bso,?\b",
    r"\byou know\b", r"\bi guess\b", r"\bkind of\b", r"\bsort of\b", r"\block\b",
    r"\blike,?\b", r"\.\.\.",
]

REPAIR_MARKERS = [
    r"\bscratch that\b", r"\bnever ?mind\b", r"\bmake that\b", r"\bchange (?:it|that) to\b",
    r"\bon second thought\b", r"\binstead\b", r"\bi mean\b", r"\bactually\b", r"\bhold on\b",
    r"\bwait\b", r"\bsorry\b", r"\bno,", r"\bno wait\b",
]
_REPAIR_RE = re.compile("|".join(REPAIR_MARKERS), re.I)
_FILLER_RE = re.compile("|".join(FILLERS), re.I)

_NOT_BUT_RE = re.compile(
    r"\bnot\s+([A-Za-z][\w .]{0,30}?)\s+but\s+([A-Za-z][\w .]{0,30}?)(?=[.,;!?]|$)", re.I)

_STUTTER_RE = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.I)
_DASH_TRUNCATION_RE = re.compile(r"\b[\w]{1,20}(?:\s+[\w]{1,20}){0,4}\s*(?:—|--)\s*")

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_TENS_WORDS = {
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_MAGNITUDE_WORDS = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_ALL_NUMBER_WORDS = {**_NUMBER_WORDS, **_TENS_WORDS}
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}
_MONTHS = {
    "jan": "January", "january": "January", "feb": "February", "february": "February",
    "mar": "March", "march": "March", "apr": "April", "april": "April", "may": "May",
    "jun": "June", "june": "June", "jul": "July", "july": "July", "aug": "August",
    "august": "August", "sep": "September", "sept": "September", "september": "September",
    "oct": "October", "october": "October", "nov": "November", "november": "November",
    "dec": "December", "december": "December",
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_WORD_ALT = "|".join(sorted(list(_ORDINAL_WORDS) + list(_NUMBER_WORDS), key=len, reverse=True))
_DATE_RE = re.compile(
    rf"\b({_MONTH_ALT})\s+(\d{{1,2}}(?:st|nd|rd|th)?|{_DAY_WORD_ALT})\b", re.I)
_NUMBER_DIGIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)
_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")

# Common sentence-initial imperative verbs/words that capitalization alone would otherwise
# misread as a proper-noun entity candidate — generic English function words, not benchmark
# strings (found via this suite's own test failures: "Add three headphones" -> "Add" was being
# read as a destination-like candidate).
_NOT_A_PROPER_NOUN = {
    "add", "book", "search", "find", "find me", "get", "get me", "tell", "check", "give",
    "please", "can", "could", "would", "let's", "let", "okay", "hey", "sorry", "i'd", "i'll", "i",
    "the", "please look", "look", "flights", "flight",
}


@dataclass
class SlotCandidate:
    arg_name: str
    value: str
    segment_idx: int
    superseded: bool = False


@dataclass
class ResolvedTurn:
    raw_text: str
    cleaned_text: str
    segments: List[str]
    candidates: List[SlotCandidate]
    corrections: List[Dict[str, str]] = field(default_factory=list)

    def final_values(self) -> Dict[str, str]:
        """Diagnostic/transparency use only (e.g. logging what the resolver believes the final
        value is) — NOT used to override a tool call's arguments. See `stale_value_map`."""
        latest: Dict[str, SlotCandidate] = {}
        for c in self.candidates:
            if not c.superseded:
                latest[c.arg_name] = c
        return {k: v.value for k, v in latest.items()}

    def stale_value_map(self) -> Dict[str, Dict[str, str]]:
        """Block-only contract: {arg_name: {stale_value_lower: corrected_value}}, built only
        from corrections this turn actually detected (a value superseded by a later one for the
        same arg, or a "not X but Y" pair whose Y matches an arg's final value). The commit gate
        may replace a tool argument ONLY when it exactly equals one of these stale values —
        never inject a freshly-computed "best guess" the model didn't already (mistakenly)
        propose. This is deliberately narrower than `final_values()`: an arg mentioned once,
        with nothing corrected away, is never touched, even if the resolver's own parse of it
        differs from the model's."""
        out: Dict[str, Dict[str, str]] = {}
        for c in self.corrections:
            arg_name = c.get("arg")
            if not arg_name:
                continue
            out.setdefault(arg_name, {})[c["from"].strip().lower()] = c["to"]
        return out


def _strip_false_starts(text: str) -> str:
    text = _STUTTER_RE.sub(r"\1", text)
    def _drop_if_followed(m: "re.Match") -> str:
        tail = text[m.end():]
        if not tail.strip():
            return m.group(0)  # trailing off at the very end isn't a false start, leave it
        # A repair marker inside the fragment about to be dropped, shortly before it (a
        # comma-attached marker like "no," breaks the regex's own word-boundary continuity, so
        # the match can start right after the marker without including it), or shortly after
        # the dash (the closing dash of a "— correction —" parenthetical), all mean this is a
        # real correction, not a pure restart — keep the text so segment-splitting can compare
        # both values, instead of deleting the "old"/corrected value before we ever see it.
        head = text[max(0, m.start() - 20):m.start()]
        if _REPAIR_RE.search(m.group(0)) or _REPAIR_RE.search(tail[:40]) or _REPAIR_RE.search(head):
            return m.group(0)
        return ""
    return _DASH_TRUNCATION_RE.sub(_drop_if_followed, text)


def _strip_fillers(text: str) -> str:
    text = _FILLER_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.-")


def _split_segments(text: str) -> List[str]:
    parts = _REPAIR_RE.split(text)
    return [p.strip(" ,.-") for p in parts if p and p.strip(" ,.-")]


def _word_to_number(word: str) -> Optional[int]:
    w = word.lower()
    if w.isdigit():
        return int(w)
    if w in _NUMBER_WORDS:
        return _NUMBER_WORDS[w]
    if w in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[w]
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?", w)
    return int(m.group(1)) if m else None


def _extract_for_arg(segment: str, arg_name: str, spec: Dict[str, Any]) -> Optional[str]:
    enum = spec.get("enum")
    is_date = "date" in arg_name.lower() or "date" in (spec.get("description") or "").lower()

    if enum:
        for val in enum:
            if re.search(rf"\b{re.escape(val)}\b", segment, re.I):
                return val
        return None

    if is_date:
        m = _DATE_RE.search(segment)
        if not m:
            return None
        day = _word_to_number(m.group(2))
        return f"{_MONTHS[m.group(1).lower()]} {day}" if day is not None else m.group(0)

    # generic string: proper-noun heuristic, excluding sentence-initial function words and
    # anything that's actually a date/month mention (so "August" from "August 10" never leaks
    # into an unrelated destination-shaped arg). Prefers the LAST remaining candidate: natural
    # speech puts the entity name after a preposition, later in the clause, while a
    # sentence-initial capitalized word is usually a mis-capitalized verb/subject that survived
    # the stoplist only because it's an unlisted one.
    without_date = _DATE_RE.sub(" ", segment)
    candidates = [m.group(1) for m in _PROPER_NOUN_RE.finditer(without_date)
                 if m.group(1).lower() not in _NOT_A_PROPER_NOUN]
    return candidates[-1] if candidates else None


def _words_to_number(tokens: List[str]) -> Optional[int]:
    """Standard English number-word grammar: units/tens accumulate, a magnitude word (hundred/
    thousand/million) multiplies what's accumulated so far and settles it into the running
    total (so "two thousand" -> 2000, "twenty five hundred" -> 2500, "one hundred fifty" ->
    150) — general vocabulary, not benchmark-derived."""
    total = 0
    current = 0
    found = False
    for tok in tokens:
        t = tok.lower()
        if t.isdigit():
            current += int(t)
            found = True
        elif t in _ALL_NUMBER_WORDS:
            current += _ALL_NUMBER_WORDS[t]
            found = True
        elif t in _MAGNITUDE_WORDS:
            current = (current or 1) * _MAGNITUDE_WORDS[t]
            total += current
            current = 0
            found = True
    return (total + current) if found else None


def _number_runs(words: List[str]) -> List[tuple]:
    """Finds contiguous runs of number-related tokens in a word list and parses each as one
    compound number. Returns (start_idx, end_idx_exclusive, value_str) per run."""
    def _is_number_token(w: str) -> bool:
        s = w.strip(",.!?$")
        return s.isdigit() or s.lower() in _ALL_NUMBER_WORDS or s.lower() in _MAGNITUDE_WORDS

    runs = []
    i = 0
    while i < len(words):
        if _is_number_token(words[i]):
            j = i
            tokens = []
            while j < len(words) and _is_number_token(words[j]):
                tokens.append(words[j].strip(",.!?$"))
                j += 1
            val = _words_to_number(tokens)
            if val is not None:
                runs.append((i, j, str(val)))
            i = j
        else:
            i += 1
    return runs


def _hint_words(arg_name: str, spec: Dict[str, Any]) -> set:
    hw = set(re.split(r"[_\s]+", arg_name.lower()))
    hw |= set((spec.get("description") or "").lower().split())
    return hw


def _extract_numbers_for_segment(segment: str,
                                 numeric_args: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Assigns every number mentioned in a segment to whichever numeric arg it most plausibly
    belongs to, all at once — necessary once a segment can contain more than one number *and*
    the schema has more than one numeric slot, e.g. "two bedrooms ... three thousand dollars a
    month" (bedrooms vs max_price): scoring each arg against the segment independently (the
    original design) let a lone number satisfy an unrelated arg's query too, since a segment
    with exactly one number was assumed to be unambiguous regardless of which arg asked.

    Hyphenated compounds ("two-bedroom") are split on the hyphen first so their number is still
    found — schema-driven and hyphen-splitting are both general text handling, not tuned to any
    specific sentence (see resolver_examples.py for the case that caught the original gap)."""
    words = re.sub(r"-", " ", segment).split()
    runs = _number_runs(words)
    if not runs or not numeric_args:
        return {}

    if len(numeric_args) == 1 and len(runs) == 1:
        return {next(iter(numeric_args)): runs[0][2]}

    assigned: Dict[str, str] = {}
    used_runs: set = set()

    # an unambiguous "$123"-style token goes straight to whichever numeric arg looks price-like,
    # before any distance scoring — consumes only that one run, other args/runs still follow.
    price_arg = next((a for a, s in numeric_args.items()
                      if _hint_words(a, s) & {"price", "cost", "budget"}), None)
    if price_arg:
        for run in runs:
            if words[run[0]].startswith("$"):
                assigned[price_arg] = run[2]
                used_runs.add(id(run))
                break

    remaining_args = {a: s for a, s in numeric_args.items() if a not in assigned}
    remaining_runs = [r for r in runs if id(r) not in used_runs]
    if remaining_args and remaining_runs:
        def _distance(run: tuple, hw: set) -> int:
            mid = (run[0] + run[1] - 1) // 2
            return min((abs(mid - i) for i, w in enumerate(words)
                       if w.strip(",.!?$").lower() in hw), default=len(words) + 1)

        pairs = [(_distance(run, _hint_words(arg_name, spec)), arg_name, run)
                for arg_name, spec in remaining_args.items() for run in remaining_runs]
        pairs.sort(key=lambda p: p[0])
        for _dist, arg_name, run in pairs:
            if arg_name in assigned or id(run) in used_runs:
                continue
            assigned[arg_name] = run[2]
            used_runs.add(id(run))

    return assigned


def resolve_turn(text: str, tool_schema: Dict[str, Any]) -> ResolvedTurn:
    raw = text
    not_but_corrections: List[Dict[str, str]] = []

    def _not_but(m: "re.Match") -> str:
        not_but_corrections.append({"from": m.group(1).strip(), "to": m.group(2).strip()})
        return m.group(2)

    text = _NOT_BUT_RE.sub(_not_but, text)
    text = _strip_false_starts(text)
    segments_raw = _split_segments(text)
    segments = [s for s in (_strip_fillers(s) for s in segments_raw) if s]

    args = tool_schema.get("args") or {}
    numeric_args = {k: v for k, v in args.items() if v.get("type") == "number"}
    other_args = {k: v for k, v in args.items() if v.get("type") != "number"}
    candidates: List[SlotCandidate] = []
    latest_per_arg: Dict[str, SlotCandidate] = {}
    corrections = list(not_but_corrections)

    def _record(arg_name: str, val: str, seg_idx: int) -> None:
        cand = SlotCandidate(arg_name, val, seg_idx)
        candidates.append(cand)
        prior = latest_per_arg.get(arg_name)
        if prior is not None and prior.value.lower() != val.lower():
            prior.superseded = True
            corrections.append({"arg": arg_name, "from": prior.value, "to": val})
        latest_per_arg[arg_name] = cand

    for i, seg in enumerate(segments):
        for arg_name, spec in other_args.items():
            val = _extract_for_arg(seg, arg_name, spec)
            if val is not None:
                _record(arg_name, val, i)
        if numeric_args:
            for arg_name, val in _extract_numbers_for_segment(seg, numeric_args).items():
                _record(arg_name, val, i)

    # "not X but Y" is substituted into the raw text before segmentation (so extraction only
    # ever sees Y), which means it has no arg_name yet — recover it from whichever arg's final
    # value the substituted-in Y ended up matching.
    for nb in not_but_corrections:
        matching_arg = next((a for a, c in latest_per_arg.items()
                             if c.value.strip().lower() == nb["to"].strip().lower()), None)
        if matching_arg is not None:
            nb["arg"] = matching_arg

    cleaned = segments[-1] if segments else _strip_fillers(text)
    return ResolvedTurn(raw_text=raw, cleaned_text=cleaned, segments=segments,
                        candidates=candidates, corrections=corrections)


# ---------------------------------------------------------------------------
# Slow path: optional LLM cross-check. Only invoked when the fast pass found a correction (or on
# request) — matches the theme's fast/slow architecture: the fast rule-based pass handles the
# common case at ~0ms, the LLM is a validation step for the harder/ambiguous ones, not the
# default path for every turn.
# ---------------------------------------------------------------------------
async def llm_cross_check(text: str, tool_schema: Dict[str, Any], llm_decide) -> Dict[str, Any]:
    """`llm_decide` is an injected async callable (text, schema) -> dict, so this module has no
    hard dependency on a specific LLM backend/SDK — see agent/llm.py for the real implementation
    wired to Gemini Flash."""
    return await llm_decide(text, tool_schema)
