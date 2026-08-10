"""When the model call fails, the broker must be told the real reason.

A text-only correction failed and the channel said "one of the reading steps ran
past its time budget — try again, or send fewer/lighter files in one message".
There were no files, and the real cause was upstream: `API Error: 529
Overloaded`. A broker following that advice would split a submission into pieces
to fix something that resolves by waiting a minute.

Two defects behind it:
  * the CLI reports API errors on STDOUT, and claude_run only read stderr, so the
    real message was thrown away
  * the only mapped failure was a timeout, and its wording assumed the cause
"""
from __future__ import annotations

import subprocess

import process_drop as pd


class FakeProc:
    def __init__(self, returncode=1, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_an_api_error_on_stdout_reaches_the_exception(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(
        stdout="API Error: 529 Overloaded. This is a server-side issue, usually temporary\n"))
    try:
        pd.claude_run("anything")
    except RuntimeError as exc:
        assert "529" in str(exc) and "Overloaded" in str(exc)
    else:
        raise AssertionError("a non-zero exit must raise")


def test_stderr_is_still_used_when_that_is_where_the_message_is(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(
        stderr="not logged in"))
    try:
        pd.claude_run("anything")
    except RuntimeError as exc:
        assert "not logged in" in str(exc)
    else:
        raise AssertionError("a non-zero exit must raise")


# --- the sentence the broker actually reads ---------------------------------

def test_an_overload_tells_the_broker_to_retry_not_to_send_less():
    msg = pd.failure_message("API Error: 529 Overloaded. Server-side issue")
    low = msg.lower()
    assert "fewer" not in low and "lighter" not in low, (
        "an upstream overload is not the broker sending too much")
    assert "again" in low or "moment" in low or "minute" in low


def test_a_rate_limit_also_reads_as_retry():
    assert "fewer" not in pd.failure_message("API Error: 429 rate limit").lower()


def test_a_genuine_timeout_still_suggests_lighter_input():
    msg = pd.failure_message("timed out after 120s").lower()
    assert "fewer" in msg or "lighter" in msg


def test_an_unrecognised_failure_is_passed_through_not_guessed_at():
    msg = pd.failure_message("something nobody has seen before")
    assert "something nobody has seen before" in msg


def test_a_login_problem_says_so():
    msg = pd.failure_message("Invalid API key / not logged in").lower()
    assert "log" in msg or "credential" in msg or "auth" in msg
