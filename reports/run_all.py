r"""Run every report a client's packet needs, in one call.

Until this existed, nothing ran the reports. Each module (mcprpt, webrpt, vinrpt)
had a command-line entry point and someone typed it by hand, so a quoting packet
came out with whatever reports happened to already be sitting in the folder. That
is precisely the thin QP COUNT5 produced on 8.11: the application pages plus the
three reports a person had run, and the rest simply absent.

This is the trigger. Given a client slug it runs each report module that applies,
leaves the PDFs in reports/out/<slug>/ where qp_build globs for them, and returns
the questions each report raised so the broker hears them rather than discovering
the gap at the underwriter's desk.

Adding a report is one entry in REGISTRY. The three existing modules return three
different shapes; the adapters below map each to one `result(...)`, so the caller
never learns those shapes and a new report does not force a rewrite here.

What is NOT registered, on purpose:
  * SAFER — the FMCSA fetch is 403 from outside the US, so its parser has to be
    written against a real capture, which only a US-IP machine can produce. A
    parser written against a guessed layout is an invented deliverable.
  * CHP — the carrier-safety page has never been captured for this project at
    all. Same rule: no blind parser.
Both are named gaps, not fake pages. Once the trigger runs on the US machine and
returns real captures, their parsers get written against that, and they join
REGISTRY as one line each.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reports"))


def result(name: str, label: str, *, pdf: str | None = None,
           questions: list[str] | None = None, problem: str | None = None) -> dict:
    """One report's outcome, in the single shape the aggregator understands.

    `pdf` is set when a page was written; `questions` are discrepancies for the
    broker to resolve; `problem` is why nothing was produced (blocked, no data on
    the app, a source that needs a US IP). A report can carry questions AND a pdf,
    or a problem and neither.
    """
    return {"name": name, "label": label, "pdf": pdf,
            "questions": list(questions or []), "problem": problem}


# --- adapters: each maps one module's own return shape to result() list -------

def _mcp(slug: str) -> list[dict]:
    import mcprpt
    r = mcprpt.run(slug)
    if r.get("ok"):
        return [result("mcp", "CA MCP", pdf=r.get("pdf"),
                       questions=r.get("questions", []))]
    return [result("mcp", "CA MCP", problem=r.get("error"))]


def _web(slug: str) -> list[dict]:
    """webrpt runs up to six captures in one call, so it yields several results."""
    import webrpt
    try:
        r = webrpt.run(slug)
    except ImportError:
        # The web captures need Playwright + a Chromium binary, kept out of the
        # base install on purpose (a 130 MB download the CAP-app path never uses).
        # On a machine where they are not set up this is an expected state, not a
        # crash — say what to do about it instead of leaking a stack trace.
        return [result("web", "web reports", problem=(
            "web captures are off on this machine — run "
            "`pip install playwright && playwright install chromium` to enable "
            "the website, Facebook, Instagram and Yelp reports"))]
    out: list[dict] = []
    findings = r.get("findings", {}) or {}
    for key, info in (r.get("made", {}) or {}).items():
        note = findings.get(key)
        out.append(result(key, info.get("label", key), pdf=info.get("pdf"),
                          questions=[note] if note else []))
    for p in r.get("problems", []) or []:
        out.append(result("web", "web report", problem=p))
    return out


def _vin(slug: str) -> list[dict]:
    import vinrpt
    r = vinrpt.run(slug)
    if r.get("ok"):
        return [result("vin", "VIN", pdf=r.get("pdf"),
                       questions=r.get("questions", []))]
    return [result("vin", "VIN", problem=r.get("error"))]


# The reports that actually exist. SAFER and CHP are absent until their parsers
# are written against a real capture — see the module docstring.
REGISTRY = [_mcp, _web, _vin]


def registered_names() -> list[str]:
    """The short name of each registered adapter, for a scope check in tests and
    for the operator to see what the trigger will attempt."""
    return [fn.__name__.lstrip("_") for fn in REGISTRY]


def run_all(slug: str, runners: list | None = None) -> dict:
    """Run every registered report for `slug` and aggregate the outcome.

    A report that throws is caught and turned into a problem: one source dying
    mid-capture must not sink the whole packet. The order of REGISTRY is the
    order of the returned lists, so a re-run reads the same way every time.
    """
    runners = REGISTRY if runners is None else runners
    results: list[dict] = []
    for fn in runners:
        try:
            results.extend(fn(slug))
        except Exception as exc:  # noqa: BLE001 - a bad report is a problem, not a crash
            label = getattr(fn, "__name__", "report").lstrip("_")
            results.append(result(label, label,
                                  problem=f"{type(exc).__name__}: {exc}"))
    made = [r["pdf"] for r in results if r.get("pdf")]
    questions = [(r["label"], q) for r in results for q in r["questions"]]
    problems = [(r["label"], r["problem"]) for r in results if r.get("problem")]
    return {"slug": slug, "results": results, "made": made,
            "questions": questions, "problems": problems}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run all reports for a client")
    ap.add_argument("--client", required=True)
    args = ap.parse_args()
    r = run_all(args.client)
    print(f"Reports for {r['slug']}: {len(r['made'])} page set(s) written")
    for p in r["made"]:
        print(f"  PDF  {p}")
    for label, q in r["questions"]:
        print(f"  ?    [{label}] {q}")
    for label, p in r["problems"]:
        print(f"  !    [{label}] {p}")


if __name__ == "__main__":
    main()
