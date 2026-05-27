"""Extract clean structured outputs from LLM responses.

Small local models like gemma4:e4b reliably solve the underlying task but
also reliably dump their entire chain-of-thought before (or around) the
real answer. Rather than fighting the model to suppress that, we extract
the structured portion in Python and discard the rest.

Each extractor returns either the cleaned text or None when nothing
parseable was found.
"""

from __future__ import annotations

import re
from typing import Optional

# Names of the sub-agents the Planner and Orchestrator are allowed to invoke.
KNOWN_AGENTS = (
    "TaskMaster",
    "Chronos",
    "Reporter",
    "CheckIn",
    "Archivist",
    "Profiler",
    "Summarizer",
)
NONE_TOKEN = "None"

# `\d+. <Agent>: <body to end-of-line>` — body never crosses a newline.
_AGENT_LINE = re.compile(
    r"(\d+)\s*\.\s*("
    + "|".join(KNOWN_AGENTS + (NONE_TOKEN,))
    + r")\s*(?::\s*([^\n]*))?",
    re.IGNORECASE,
)


_ANSWER_TAG_RE = re.compile(r"<answer>\s*(.*?)\s*(?:</answer>|$)", re.IGNORECASE | re.DOTALL)


def extract_answer_tag(text: str) -> Optional[str]:
    """Pull content between <answer> and </answer> tags.

    The closing tag is optional because we set it as a stop_sequence — the
    model halts as it emits </answer>, so the literal text may not contain
    it. Returns the LAST match (in case the model produced multiple tags
    during its dump).
    """
    if not text:
        return None
    matches = _ANSWER_TAG_RE.findall(text)
    if not matches:
        return None
    return matches[-1].strip() or None


def extract_plan(text: str) -> Optional[str]:
    """Pull the numbered plan lines out of the Planner's output."""
    return _extract_numbered_agent_lines(text, allow_none=True)


def extract_action_log(text: str) -> Optional[str]:
    """Pull the numbered action log lines out of the Orchestrator's output.

    Models often echo the plan AND emit the results. We dedup by step number,
    keeping the LAST occurrence — the actual result usually comes after the
    plan-echo in the dump.
    """
    if not text:
        return None
    if re.search(r"no\s+actions\s+taken", text, re.IGNORECASE):
        # Only return this if there are no real agent lines too.
        if not _AGENT_LINE.search(text):
            return "No actions taken."
    return _extract_numbered_agent_lines(text, allow_none=False)


def extract_last_paragraph(text: str) -> Optional[str]:
    """Return the trailing run of non-meta sentences from a model dump.

    Personalizer dumps reasoning followed by the answer, often without
    paragraph breaks (e.g. "I will keep the tone warm.No actions were taken.").
    Strategy: split into sentences, walk backwards collecting non-meta
    sentences until we hit a meta one, then return them in original order.
    """
    return _extract_trailing_non_meta_sentences(text, require_markdown=False)


def extract_formatted_message(text: str) -> Optional[str]:
    """Return the trailing run of non-meta sentences.

    We deliberately don't require MarkdownV2 markers — gemma often dumps
    reasoning that ALSO contains `**bold**` and other markdown-ish syntax
    in its CoT, which would beat the real (sometimes unformatted) answer
    at the very end.
    """
    return _extract_trailing_non_meta_sentences(text, require_markdown=False)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

_META_PREFIXES = (
    "draft",
    "thinking",
    "constraint",
    "mental sandbox",
    "let's",
    "lets",
    "applying",
    "best format",
    "refining",
    "original:",
    "format check",
    "verbatim check",
    "this meets",
    "this looks",
    "this is",
    "output:",
    "output the",
    "input:",
    "rules:",
    "examples",
    "processing:",
    "drafting",
    "final answer",
    "the result",
    "the formatted",
    "the user wants",
    "the user provided",
    "the plan",
    "the rules",
    "the instructions",
    "if the action",
    "i need",
    "i must",
    "i should",
    "i will",
    "i'll",
    "wait,",
    "step",
    "first,",
    "next,",
)


# Sentence splitter that allows zero whitespace after punctuation (so
# "concise.No" splits at the period).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s*(?=[A-Z\"\'(])")

# MarkdownV2 marker presence (asterisk, underscore, backtick, or escape).
_MD_MARKER = re.compile(r"[*_`]|\\[.\-_*!\[\]()~`>#+=|{}]")


def _strip_wrapping_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] in ("\"", "'") and s[-1] == s[0]:
        return s[1:-1].strip()
    return s


def _extract_trailing_non_meta_sentences(
    text: str, *, require_markdown: bool
) -> Optional[str]:
    """Walk backwards over sentences, keeping non-meta ones until we hit meta.

    Returns the kept sentences re-joined in original order, or None.
    """
    if not text:
        return None
    flat = text.replace("\n", " ").strip()
    if not flat:
        return None

    # Drop any wrapping quotes Scribe sometimes adds around its output.
    flat = _strip_wrapping_quotes(flat)

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(flat) if s.strip()]
    if not sentences:
        return None

    kept: list[str] = []
    for s in reversed(sentences):
        s_clean = _strip_inline_meta(s)
        if not s_clean:
            continue
        if _looks_like_meta(s_clean):
            break
        if require_markdown and not _MD_MARKER.search(s_clean):
            # Allow non-MD sentences to be skipped over (don't break) — we
            # might still find an MD-formatted one further back.
            continue
        kept.append(s_clean)

    if not kept:
        return None
    return " ".join(reversed(kept))


# Pattern that detects MarkdownV2 rule-explanation lines like
# "*bold* action verbs" or "`code` for ids".
_META_RULE_RE = re.compile(
    r"[*_`][a-z]+[*_`]\s+(action|word|verb|noun|for|formatting|rule)",
    re.IGNORECASE,
)


def _looks_like_meta(line: str) -> bool:
    low = line.lower().lstrip("-*0123456789. \t")
    if any(low.startswith(p) for p in _META_PREFIXES):
        return True
    if _META_RULE_RE.search(line):
        return True
    return False


def _strip_inline_meta(line: str) -> str:
    """Strip leading parenthetical asides and meta-prefixed clauses.

    Common patterns:
      "(I will escape the period.)<answer>"   -> "<answer>"
      "Draft 1: <answer>"                     -> "<answer>"
      "Final answer: <answer>"                -> "<answer>"
    """
    line = line.strip()

    # 1. Drop a leading `(...)` parenthetical aside, possibly repeated.
    m = re.match(r"^\(([^)]*)\)\s*(.*)$", line)
    while m and m.group(2).strip():
        line = m.group(2).strip()
        m = re.match(r"^\(([^)]*)\)\s*(.*)$", line)

    # 2. Drop a meta prefix ending in `:`.
    if ":" in line:
        prefix, _, rest = line.partition(":")
        if _looks_like_meta(prefix + ":"):
            line = rest.strip()

    return line


_HEAD = re.compile(
    r"(\d+)\s*\.\s*("
    + "|".join(KNOWN_AGENTS + (NONE_TOKEN,))
    + r")\b",
    re.IGNORECASE,
)


def _extract_numbered_agent_lines(text: str, *, allow_none: bool) -> Optional[str]:
    """Slice the text between `N. AGENT` heads.

    The body of each entry is everything from the end of one head match up to
    the start of the next (or end of text). This survives the case where the
    model concatenates two plan lines onto one physical line, e.g.
    "1. TaskMaster: X1. TaskMaster: Y". `[^\\n]*` regexes can't recover the
    second body in that scenario; slicing between heads does.
    """
    if not text:
        return None

    heads = list(_HEAD.finditer(text))
    if not heads:
        return None

    by_step: dict[int, tuple[str, str]] = {}
    for i, m in enumerate(heads):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        agent = m.group(2)
        body_start = m.end()
        body_end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[body_start:body_end].lstrip(": \t").rstrip()
        # Trim trailing newlines + bullet noise.
        body = body.split("\n", 1)[0].strip()
        if agent.lower() == NONE_TOKEN.lower():
            if allow_none:
                by_step[n] = (NONE_TOKEN, "")
            continue
        if not body:
            continue
        by_step[n] = (agent, body)  # last-wins for duplicate step numbers

    if not by_step:
        return None

    ordered = [by_step[n] for n in sorted(by_step.keys())]
    out_lines = []
    for i, (agent, body) in enumerate(ordered, 1):
        if agent == NONE_TOKEN:
            out_lines.append(f"{i}. None")
        else:
            out_lines.append(f"{i}. {agent}: {body}")
    return "\n".join(out_lines)
