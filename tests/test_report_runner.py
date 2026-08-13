"""The report trigger runs every report and collects what each one said.

Until this existed, nothing ran the reports. Each module had a CLI and someone
typed it by hand, so a QP came out with whatever happened to be sitting in the
folder — which is exactly the "poor QP" that COUNT5 delivered on 8.11: fourteen
app pages and three reports that had been run manually, the other six missing.

run_all is the missing trigger. Given a client it runs each report module, leaves
the PDFs where qp_build globs for them, and returns the questions each one raised
so the broker hears them. The modules have three different return shapes; the
adapters map them to one, so adding a report is one entry, not a rewrite here.

These tests inject fake runners. The real ones reach the network (and two of them
need a US IP), so what is worth testing here is the orchestration: it aggregates,
it survives a report that throws, and it never lets one bad report sink the rest.
"""
from __future__ import annotations

import run_all as ra


def _fake(name, label, pdf=None, questions=(), problem=None):
    def runner(slug):
        return [ra.result(name, label, pdf=pdf, questions=list(questions),
                          problem=problem)]
    return runner


def test_it_collects_made_pdfs_questions_and_problems_across_reports():
    runners = [
        _fake("mcp", "CA MCP", pdf="/out/x/mcp.pdf",
              questions=["Legal name differs from the app."]),
        _fake("vin", "VIN", pdf="/out/x/vin.pdf"),
        _fake("safer", "SAFER", problem="403 from this IP — needs a US fetch"),
    ]
    r = ra.run_all("x-towing", runners=runners)

    assert r["made"] == ["/out/x/mcp.pdf", "/out/x/vin.pdf"]
    assert ("CA MCP", "Legal name differs from the app.") in r["questions"]
    assert ("SAFER", "403 from this IP — needs a US fetch") in r["problems"]


def test_a_report_that_throws_does_not_sink_the_others():
    def explodes(slug):
        raise RuntimeError("network died mid-capture")

    runners = [
        _fake("mcp", "CA MCP", pdf="/out/x/mcp.pdf"),
        explodes,
        _fake("vin", "VIN", pdf="/out/x/vin.pdf"),
    ]
    r = ra.run_all("x-towing", runners=runners)

    # both good reports still made it
    assert r["made"] == ["/out/x/mcp.pdf", "/out/x/vin.pdf"]
    # and the crash is surfaced as a problem, not swallowed
    assert any("network died mid-capture" in p for _, p in r["problems"])


def test_one_web_run_can_yield_several_reports():
    def web(slug):
        return [
            ra.result("website", "Website", pdf="/out/x/website.pdf"),
            ra.result("yelp", "Yelp", problem="Yelp served a block page"),
            ra.result("street", "Google street", pdf="/out/x/street.pdf"),
        ]
    r = ra.run_all("x-towing", runners=[web])

    assert r["made"] == ["/out/x/website.pdf", "/out/x/street.pdf"]
    assert ("Yelp", "Yelp served a block page") in r["problems"]


def test_no_runners_is_empty_not_a_crash():
    r = ra.run_all("x-towing", runners=[])
    assert r == {"slug": "x-towing", "results": [], "made": [],
                 "questions": [], "problems": []}


def test_the_default_registry_names_the_reports_that_exist_today():
    # A guard on scope: the trigger wires the reports that are actually built.
    # SAFER and CHP are deliberately absent until their parsers are written
    # against a real capture — a blind parser would be an invented deliverable.
    names = ra.registered_names()
    assert "mcp" in names
    assert "vin" in names
    assert "web" in names
    assert "safer" not in names, "SAFER has no parser yet — do not register a guess"
    assert "chp" not in names, "CHP page has never been captured — do not register a guess"
