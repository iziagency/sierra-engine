"""No module may hardcode an absolute path.

This is the defect that got furthest without being noticed. process_drop.py held
`ROOT = Path(r"C:\\dev\\sierra-pacific")` and client_match.py held the clients
folder the same way, so the engine only ever worked in one directory on one
machine. The install guide tells the operator to clone to `C:\\sierra-pacific`,
which means every drop on a correctly installed machine failed with

    [Errno 2] No such file or directory:
    'C:\\dev\\sierra-pacific/app-form/config/client_data.example.json'

Two things hid it. The tests monkeypatch `CLIENTS`, so they never touch the real
path; and they ran on the one machine where that directory happens to exist. A
fresh clone was verified by running its code — on that same machine. Running the
code proves nothing about a path if the path is right where you are standing.

So this test does not exercise behaviour. It reads the source and refuses the
shape of the mistake, which is the only way to catch a path that is correct on
the machine running the suite and wrong everywhere else.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Any string literal that starts at a Windows drive letter or a POSIX root:
# Path(r"C:\..."), ROOT = r"C:\...", "/Users/jc/...", '/home/...'. The first
# version of this guard only caught Path(...) and missed a bare
# `ROOT = r"C:\dev\sierra-pacific\app-form"` in fill_app.py — a string the engine
# ran through subprocess, which failed on a Mac where that path is meaningless.
# A drive-letter or leading-slash literal is a machine naming itself; relative
# literals resolved against __file__ are fine and do not match.
ABSOLUTE_LITERAL = re.compile(
    r"""r?["']"""                                    # start of a string literal
    r"""(?:[A-Za-z]:[\\/]"""                          # C:\  or  C:/  (Windows drive)
    r"""|/(?:Users|home|opt|usr|var|tmp|Library|Applications|private|etc|mnt|root|Volumes)/)"""
)  # a POSIX filesystem root — NOT a URL path like "/api/..." or "/query.asp"

# The engine's own doctrine, in one line: everything hangs off this file's own
# location. Anything else is a machine talking about itself.
DERIVED = "Path(__file__)"


def _tracked_python() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    files = [ROOT / line for line in out.stdout.splitlines() if line]
    # Test files legitimately hold fake absolute paths as fixtures — a simulated
    # `/usr/sbin/cupsd` process line, a `C:\tmp\...` stand-in return value. The
    # guard is about code that RUNS on another machine, not data a test invents.
    # make_handwritten_sample.py is a Windows-only fixture generator (it needs a
    # Windows handwriting font); it never runs on the client's machine or in the
    # engine, so its one Windows path is allowed.
    skip = {"make_handwritten_sample.py"}
    return [f for f in files if "tests" not in f.parts and f.name not in skip]


def test_no_module_hardcodes_an_absolute_path():
    offenders = []
    for path in _tracked_python():
        if path.name == Path(__file__).name:
            continue                      # this file quotes the mistake on purpose
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                  # a path inside a comment is documentation
            if ABSOLUTE_LITERAL.search(line):
                rel = path.relative_to(ROOT).as_posix()
                offenders.append(f"{rel}:{n}: {line.strip()}")

    assert not offenders, (
        "these paths only exist on one machine, so the engine only runs on that "
        "machine. Derive them from " + DERIVED + " instead:\n  "
        + "\n  ".join(offenders))


def test_the_modules_that_locate_the_repo_derive_it_from_their_own_file():
    """The three that actually matter, named, so a regression says which one."""
    for rel, symbol in (("watcher/process_drop.py", "ROOT"),
                        ("watcher/client_match.py", "CLIENTS"),
                        ("reports/qp_build.py", "ROOT")):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assignment = next((ln for ln in text.splitlines()
                           if ln.startswith(f"{symbol} =")), "")
        assert assignment, f"{rel} no longer defines {symbol} at module level"
        assert DERIVED in assignment, (
            f"{rel} sets {symbol} to {assignment.strip()!r}. It has to come from "
            f"{DERIVED}, or the engine is pinned to whichever machine wrote it.")
