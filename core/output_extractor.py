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
    """Return the last meaningful sentence from a model's prose dump.

    Personalizer outputs drafts and reasoning; the final usable sentence is
    typically the last non-empty, non-meta line.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        cleaned = _strip_inline_meta(line)
        if cleaned and not _looks_like_meta(cleaned):
            return cleaned
    return lines[-1]


def extract_formatted_message(text: str) -> Optional[str]:
    """Return the last line that looks like a finished MarkdownV2 message."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    md_markers = re.compile(r"[*_`]|\\[.\-_*!\[\]()~`>#+=|{}]")
    for line in reversed(lines):
        cleaned = _strip_inline_meta(line)
        if not cleaned or _looks_like_meta(cleaned):
            continue
        if md_markers.search(cleaned):
            return cleaned

    for line in reversed(lines):
        cleaned = _strip_inline_meta(line)
        if cleaned and not _looks_like_meta(cleaned):
            return cleaned
    return lines[-1]


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
    "input:",
    "rules:",
    "examples",
    "processing:",
    "drafting",
    "the user wants",
    "the plan",
    "i need",
    "i must",
    "i should",
    "wait,",
    "step",
    "first,",
    "next,",
)


def _looks_like_meta(line: str) -> bool:
    low = line.lower().lstrip("-*0123456789. \t")
    return any(low.startswith(p) for p in _META_PREFIXES)


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


def _extract_numbered_agent_lines(text: str, *, allow_none: bool) -> Optional[str]:
    """Shared helper for plan / action-log extraction.

    Strategy: find every `N. AGENT: body` occurrence in the text, group by N,
    take the LAST body per N (models often emit the plan-echo before the real
    result). Renumber the final list 1..k.
    """
    if not text:
        return None

    by_step: dict[int, tuple[str, str]] = {}  # step number -> (agent, body)
    for m in _AGENT_LINE.finditer(text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        agent = m.group(2)
        body = (m.group(3) or "").strip()
        if agent.lower() == NONE_TOKEN.lower():
            if allow_none:
                by_step[n] = (NONE_TOKEN, "")
            continue
        if not body:
            continue
        # Last occurrence wins.
        by_step[n] = (agent, body)

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
