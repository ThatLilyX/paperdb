"""Reading a paper with the Claude Code CLI, against a fake ``claude`` process.

The real CLI is never run here: each test hands ``summarize`` a stand-in for
``subprocess.run`` and checks what was asked of it and what came back.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from paperdb.summarize import SummarizeError, summarize

PDF = Path("/papers/db/smith2024graph Graph Neural Networks at Scale.pdf")
BULLETS = ["Samples neighbours instead of the whole graph.", "Scales to 1B edges.", "Beats GCN by 4%."]


def reply(payload: dict, returncode: int = 0):
    """A fake ``subprocess.run`` that answers with ``payload`` as the CLI's JSON."""
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout=json.dumps(payload), stderr="")

    run.calls = calls
    return run


def success(bullets=BULLETS, cost=0.42) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps({"bullets": bullets}),
        "structured_output": {"bullets": bullets},
        "total_cost_usd": cost,
    }


def test_the_bullets_and_cost_come_back_from_the_structured_output():
    summary = summarize(PDF, "Graph Neural Networks at Scale", run=reply(success()))

    assert summary.bullets == BULLETS
    assert summary.cost_usd == pytest.approx(0.42)


def test_claude_reads_the_pdf_itself_with_only_the_read_tool_and_a_budget():
    run = reply(success())

    summarize(PDF, "Graph Neural Networks at Scale", run=run, budget=0.75)

    command = run.calls[0]
    assert command[:2] == ["claude", "-p"]
    assert str(PDF) in command[2], "the prompt names the file to read"
    assert command[command.index("--tools") + 1] == "Read", "Read is the only tool that exists"
    assert "--restricted" in command, "file tools confined to the paper's folder"
    assert "--strict-mcp-config" in command, "none of the user's MCP servers"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--add-dir") + 1] == str(PDF.parent)
    assert command[command.index("--max-budget-usd") + 1] == "0.75"
    assert "--json-schema" in command
    assert "--no-session-persistence" in command
    assert "--bare" not in command, "--bare skips the user's login"


def test_a_model_is_passed_only_when_asked_for():
    default, chosen = reply(success()), reply(success())

    summarize(PDF, "T", run=default)
    summarize(PDF, "T", run=chosen, model="sonnet")

    assert "--model" not in default.calls[0]
    assert chosen.calls[0][chosen.calls[0].index("--model") + 1] == "sonnet"


def test_bullets_are_trimmed_and_blank_ones_dropped():
    summary = summarize(PDF, "T", run=reply(success(["  One.  ", "", "Two.", "  ", "Three."])))

    assert summary.bullets == ["One.", "Two.", "Three."]


@pytest.mark.parametrize(
    "payload, message",
    [
        pytest.param(
            {"subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"},
            "Not logged in", id="not-logged-in",
        ),
        pytest.param(
            {"subtype": "error_max_budget_usd", "is_error": True, "result": ""},
            "error_max_budget_usd", id="over-budget",
        ),
        pytest.param({"subtype": "success", "is_error": False, "result": "sure!"}, "no bullets", id="no-structure"),
        pytest.param(success(bullets=[]), "no bullets", id="empty"),
        pytest.param(success(bullets=["   "]), "no bullets", id="only-blank"),
    ],
)
def test_a_reply_without_usable_bullets_is_an_error_not_an_empty_section(payload, message):
    with pytest.raises(SummarizeError, match=message):
        summarize(PDF, "T", run=reply(payload))


@pytest.mark.parametrize("count", [2, 7])
def test_a_bullet_count_outside_three_to_six_is_refused(count):
    with pytest.raises(SummarizeError, match="bullets"):
        summarize(PDF, "T", run=reply(success([f"Point {i}." for i in range(count)])))


def test_a_failed_read_still_reports_what_it_cost():
    payload = {"subtype": "error_max_budget_usd", "is_error": True, "result": "", "total_cost_usd": 3.1}

    with pytest.raises(SummarizeError) as raised:
        summarize(PDF, "T", run=reply(payload))

    assert raised.value.cost_usd == pytest.approx(3.1)


def test_output_that_is_not_json_is_an_error():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    with pytest.raises(SummarizeError, match="boom"):
        summarize(PDF, "T", run=run)


def test_a_missing_claude_cli_says_how_to_get_it():
    def run(command, **kwargs):
        raise FileNotFoundError(command[0])

    with pytest.raises(SummarizeError, match="Claude Code"):
        summarize(PDF, "T", run=run)


def test_a_paper_that_takes_too_long_is_an_error():
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    with pytest.raises(SummarizeError, match="took longer"):
        summarize(PDF, "T", run=run)
