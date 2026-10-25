"""Summarizing a paper's full text into the bullets under "What to focus on".

The reading is done by the Claude Code CLI, which opens the PDF itself: every
page, figures and equations included, with no text-extraction step in between.
It runs on the user's own Claude login, so paperdb needs no API key. Read is the
only tool Claude has, confined to the paper's folder, and none of the user's MCP
servers load, so it can look but never change anything.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CLAUDE = "claude"

# A spending cap per paper, enforced by the CLI. A 5-page paper cost $0.55 on
# Opus at list price; a 30-page one reads about six times as many pages.
MAX_BUDGET_USD = 3.0
TIMEOUT_SECONDS = 15 * 60

MIN_BULLETS, MAX_BULLETS = 3, 6

SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "minItems": MIN_BULLETS,
            "maxItems": MAX_BULLETS,
        }
    },
    "required": ["bullets"],
}

PROMPT = """\
Read the whole paper in the PDF at {path!r} ("{title}"). Use the Read tool and page \
through every page; it reads at most 20 pages per call.

Then write {low} to {high} bullets on what is new in this paper: the ideas, methods \
and results a reader should focus on, in order of importance. Base them on the full \
text, not just the abstract.

Each bullet is one plain sentence of at most 30 words. Include the key number where \
the paper has one. No markdown, no leading bullet characters, no section numbers."""


class SummarizeError(RuntimeError):
    """Raised when Claude could not produce bullets for a paper.

    Carries what the attempt cost, so a failed read still counts in the total.
    """

    def __init__(self, message: str, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


@dataclass
class Summary:
    """One paper's bullets, with what reading it took."""

    bullets: list[str]
    cost_usd: float = 0.0
    seconds: float = 0.0


def command(pdf: Path, title: str, model: str | None = None, budget: float = MAX_BUDGET_USD) -> list[str]:
    """The ``claude`` invocation that reads one paper."""
    args = [
        CLAUDE,
        "-p",
        PROMPT.format(path=str(pdf), title=title, low=MIN_BULLETS, high=MAX_BULLETS),
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        # Read is the only tool that exists, not merely the only one pre-approved:
        # the user's own settings may approve anything else automatically.
        "--tools", "Read",
        "--allowedTools", "Read",
        "--restricted",
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--add-dir", str(pdf.parent),
        "--max-budget-usd", f"{budget:g}",
        "--no-session-persistence",
    ]
    if model:
        args += ["--model", model]
    return args


def summarize(
    pdf: Path,
    title: str,
    *,
    model: str | None = None,
    budget: float = MAX_BUDGET_USD,
    run=subprocess.run,
) -> Summary:
    """Have Claude read ``pdf`` and return its bullets.

    Raises :class:`SummarizeError` rather than returning an empty summary, so a
    failed read never blanks a section.
    """
    started = time.monotonic()
    try:
        done = run(
            command(pdf, title, model, budget),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=pdf.parent,
        )
    except FileNotFoundError:
        raise SummarizeError(
            f"`{CLAUDE}` was not found. Install Claude Code (https://claude.com/claude-code) and log in."
        ) from None
    except subprocess.TimeoutExpired:
        raise SummarizeError(f"Claude took longer than {TIMEOUT_SECONDS // 60} minutes") from None

    try:
        reply = json.loads(done.stdout)
    except ValueError:
        detail = (done.stderr or done.stdout or "").strip() or f"exit status {done.returncode}"
        raise SummarizeError(f"Claude failed: {detail}") from None

    cost = float(reply.get("total_cost_usd") or 0.0)
    if reply.get("is_error") or reply.get("subtype") != "success":
        detail = (reply.get("result") or "").strip() or reply.get("subtype") or "unknown error"
        raise SummarizeError(f"Claude failed: {detail}", cost)

    bullets = [
        text.strip()
        for text in (reply.get("structured_output") or {}).get("bullets") or []
        if isinstance(text, str) and text.strip()
    ]
    if not bullets:
        raise SummarizeError("Claude returned no bullets", cost)
    if not MIN_BULLETS <= len(bullets) <= MAX_BULLETS:
        raise SummarizeError(
            f"Claude returned {len(bullets)} bullets, not {MIN_BULLETS} to {MAX_BULLETS}", cost
        )
    return Summary(
        bullets=bullets,
        cost_usd=cost,
        seconds=time.monotonic() - started,
    )
