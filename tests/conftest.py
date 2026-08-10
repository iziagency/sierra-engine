"""Pytest path setup so tests can `import formatting`, `import fill_app`,
`import rts_fill`, etc. straight from their real locations.

The codebase has no package structure - every script already reaches across
directories with sys.path.insert (see watcher/lossruns.py, reports/qp_build.py)
rather than relative imports, so tests follow the same convention instead of
inventing packaging the app itself doesn't use.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _sub in ("shared", "watcher", "reports", "app-form/scripts"):
    _p = str(ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


# `import slack_engine` calls load_env() at module level, and load_env exits the
# process when its env file is missing. That hard exit is deliberate and stays:
# a supervisor that silently comes up holding no tokens is worse than one that
# refuses to start. But it also means a machine with no watcher/.env cannot even
# COLLECT the suite - pytest dies with INTERNALERROR before running a single
# test. That is every fresh clone, including the 24/7 machine, where only
# .env.sierra is installed.
#
# So: if watcher/.env is there, nothing changes and the tests see exactly what
# they have always seen. If it is not, point the engine at a throwaway file with
# unusable values. No test asserts on token contents - they would be asserting on
# whatever workspace the developer happens to be pointed at.
if not (ROOT / "watcher" / ".env").exists() and "SIERRA_ENV_FILE" not in os.environ:
    _stub = Path(tempfile.gettempdir()) / "sierra-engine-tests.env"
    _stub.write_text(
        "SLACK_BOT_TOKEN=xoxb-not-a-real-token-tests-only\n"
        "SLACK_APP_TOKEN=xapp-not-a-real-token-tests-only\n"
        "SLACK_ALLOWED_CHANNELS=tests-never-post\n",
        encoding="utf-8",
    )
    # Absolute, because load_env resolves it as HERE / <name> and an absolute
    # path is what makes that join land outside the repo instead of inside it.
    os.environ["SIERRA_ENV_FILE"] = str(_stub)
