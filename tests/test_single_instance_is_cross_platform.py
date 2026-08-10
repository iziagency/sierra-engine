"""The instance lock asked Windows a question the Mac Studio cannot answer.

`claim_single_instance` shelled out to `tasklist`, which does not exist on
macOS. The engine only reaches that code when `engine.pid` is left behind — and
a file called `engine.pid` is *always* left behind by a power cut or a reboot,
which is precisely the moment an unattended machine has to come back by itself.
Under launchd the crash would not even be visible: exit 1 looks like a failure
worth retrying, so the service restarts, crashes, and restarts, forever, while
Slack stays quiet.

The lock itself is worth keeping. Two Socket Mode connections on one Slack app
make Slack hand each event to exactly one of them, so half the drops vanish
without an error anywhere. That is why the probe has to answer honestly rather
than be deleted.

`os.kill(pid, 0)`, the usual cross-platform trick, is deliberately not used:
CPython maps `os.kill` on Windows onto TerminateProcess, so asking "are you
alive?" would kill the process being asked about.
"""
from __future__ import annotations

import subprocess

import slack_engine as se


class FakeRun:
    """Stands in for the OS probe and records what was asked."""

    def __init__(self, stdout: str = "", raises: type[Exception] | None = None):
        self.stdout = stdout
        self.raises = raises
        self.argv: list[str] | None = None

    def __call__(self, argv, **kwargs):
        self.argv = argv
        if self.raises:
            raise self.raises("no such tool")
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


def probe(monkeypatch, *, posix: bool, stdout: str = "", raises=None) -> FakeRun:
    run = FakeRun(stdout, raises)
    monkeypatch.setattr(se.os, "name", "posix" if posix else "nt")
    monkeypatch.setattr(se.subprocess, "run", run)
    return run


def test_on_macos_it_asks_ps_not_tasklist(monkeypatch):
    run = probe(monkeypatch, posix=True, stdout="python3 slack_engine.py --env .env.sierra\n")
    assert se.pid_is_live_engine(4321) is True
    assert run.argv[0] == "ps"
    assert "4321" in run.argv


def test_on_macos_a_recycled_pid_is_not_our_engine(monkeypatch):
    # After a reboot engine.pid routinely names some unrelated process. The
    # number existing is not evidence; the command line is.
    probe(monkeypatch, posix=True, stdout="/usr/sbin/cupsd -l\n")
    assert se.pid_is_live_engine(4321) is False


def test_on_macos_a_dead_pid_prints_nothing(monkeypatch):
    probe(monkeypatch, posix=True, stdout="")
    assert se.pid_is_live_engine(4321) is False


def test_on_windows_it_still_asks_tasklist(monkeypatch):
    run = probe(monkeypatch, posix=False, stdout='"python.exe","4321","Console"\n')
    assert se.pid_is_live_engine(4321) is True
    assert run.argv[0] == "tasklist"


def test_on_windows_no_match_means_dead(monkeypatch):
    probe(monkeypatch, posix=False, stdout="INFO: No tasks are running which match the criteria.\n")
    assert se.pid_is_live_engine(4321) is False


def test_a_missing_probe_tool_does_not_crash_the_boot(monkeypatch):
    # If the probe itself cannot run we have no evidence of a second engine.
    # Refusing to start would turn a missing binary into a silent outage that
    # launchd would retry forever, so the engine comes up and says so.
    probe(monkeypatch, posix=True, raises=FileNotFoundError)
    assert se.pid_is_live_engine(4321) is False


def test_a_stale_pid_file_does_not_stop_the_engine(tmp_path, monkeypatch):
    lock = tmp_path / "engine.pid"
    lock.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path)
    monkeypatch.setattr(se, "pid_is_live_engine", lambda pid: False)
    se.claim_single_instance()                       # must not SystemExit
    assert lock.read_text(encoding="utf-8").strip() == str(se.os.getpid())


def test_a_live_engine_still_blocks_a_second_one(tmp_path, monkeypatch):
    lock = tmp_path / "engine.pid"
    lock.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path)
    monkeypatch.setattr(se, "pid_is_live_engine", lambda pid: True)
    try:
        se.claim_single_instance()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("a second engine must refuse to start")
    assert lock.read_text(encoding="utf-8").strip() == "4321"


def test_a_corrupt_pid_file_is_not_a_reason_to_stay_down(tmp_path, monkeypatch):
    lock = tmp_path / "engine.pid"
    lock.write_text("half-written", encoding="utf-8")
    monkeypatch.setattr(se, "HERE", tmp_path)
    se.claim_single_instance()
    assert lock.read_text(encoding="utf-8").strip() == str(se.os.getpid())
