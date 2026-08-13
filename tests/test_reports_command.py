"""`Reports <client>` in the channel runs every report and answers with the lot.

This is the Slack half of the trigger. A broker types "Reports LAKES1" (or
"Reportes ...") and the engine runs run_all, drops the PDFs where the QP builder
finds them, and replies with what it made and — the part that matters — every
question and every gap, so the packet's holes are spoken, not silently shipped.

run_all is monkeypatched here: the real one reaches the network. What is tested
is that the command is recognised, the client is resolved, and the reply carries
the made count, the questions, and the problems.
"""
from __future__ import annotations

import process_drop as pd
import slack_engine as se


class Say:
    def __init__(self):
        self.messages = []

    def __call__(self, text="", thread_ts=None, **kw):
        self.messages.append(text)

    @property
    def all_text(self):
        return "\n".join(self.messages)


def _fake_run_all(**by_slug):
    def run_all(slug, runners=None):
        return by_slug[slug]
    return run_all


def test_a_line_that_is_not_a_reports_command_is_left_alone():
    say = Say()
    assert se.try_reports_command("here's the app for Nora's", "", say, "t1") is False
    assert say.messages == []


def test_prose_that_merely_starts_with_report_is_not_a_command():
    # "report on the client" and "report says ..." open with the keyword but name
    # no client — they must fall through, not draw a "which client?" reply.
    say = Say()
    assert se.try_reports_command("report on the client", "", say, "t1") is False
    assert se.try_reports_command("report says the truck is a 2016", "", say,
                                  "t1") is False
    assert say.messages == []


def test_it_asks_which_client_when_none_is_named(monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", pd.CLIENTS)  # unchanged; no dossier resolves
    say = Say()
    handled = se.try_reports_command("Reports", "", say, "t1")
    assert handled is True
    assert "which client" in say.all_text.lower()


def test_it_runs_the_reports_and_names_what_it_made(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    (tmp_path / "lakeside-towing-llc").mkdir()
    (tmp_path / "lakeside-towing-llc" / "state.json").write_text(
        '{"sp_code": "LAKES1"}', encoding="utf-8")

    import run_all as ra
    monkeypatch.setattr(ra, "run_all", _fake_run_all(**{
        "lakeside-towing-llc": {
            "slug": "lakeside-towing-llc",
            "made": ["/out/mcp.pdf", "/out/street.pdf"],
            "questions": [("CA MCP", "Carrier on the app differs from the state's.")],
            "problems": [("SAFER", "403 from this IP — run it on the US machine")],
            "results": [],
        }}))

    say = Say()
    handled = se.try_reports_command("Reports LAKES1", "lakeside-towing-llc",
                                     say, "t1")
    assert handled is True
    body = say.all_text
    assert "2" in body                                   # two pages made
    assert "Carrier on the app differs" in body          # the question surfaces
    assert "403 from this IP" in body                    # the gap surfaces
    assert "SAFER" in body


def test_the_reply_says_so_when_nothing_could_be_made(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "CLIENTS", tmp_path)
    (tmp_path / "x-towing").mkdir()
    (tmp_path / "x-towing" / "state.json").write_text('{"sp_code": "XX1"}',
                                                      encoding="utf-8")

    import run_all as ra
    monkeypatch.setattr(ra, "run_all", _fake_run_all(**{
        "x-towing": {"slug": "x-towing", "made": [], "questions": [],
                     "problems": [("CA MCP", "no CA number on the app")],
                     "results": []}}))

    say = Say()
    se.try_reports_command("Reportes XX1", "x-towing", say, "t1")
    body = say.all_text.lower()
    assert "no" in body and "ca number" in body
