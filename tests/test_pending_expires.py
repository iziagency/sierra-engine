"""A confirmation nobody answered stops meaning anything.

After the 8.1 test round, `pending.json` still held a Shoreline thread from
hours earlier plus entries going back days. A `yes` typed into any of those old
threads would have signed off work the broker had long stopped looking at — and
the signature goes into a hash-chained audit log that JC's underwriters read.

A Slack thread_ts IS a unix timestamp, so age comes straight off the key: no
extra bookkeeping, nothing to keep in sync. Reading never mutates the file; the
prune at startup is what keeps it from growing forever.
"""
from __future__ import annotations

import json

import slack_engine as se

NOW = 1785640000.0                  # 2026-08-01 ~22:00
HOUR = 3600.0


def write(tmp_path, monkeypatch, entries: dict):
    p = tmp_path / "pending.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(se, "PENDING", p)
    return p


def test_a_fresh_confirmation_is_still_live(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, {str(NOW - 2 * HOUR): {"slug": "shoreline"}})
    assert se.pending_map(now=NOW)


def test_a_day_old_confirmation_is_gone(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, {str(NOW - 30 * HOUR): {"slug": "shoreline"}})
    assert se.pending_map(now=NOW) == {}


def test_the_boundary_is_the_ttl_not_a_guess(tmp_path, monkeypatch):
    ttl = se.PENDING_TTL_HOURS
    write(tmp_path, monkeypatch, {
        str(NOW - (ttl - 1) * HOUR): {"slug": "inside"},
        str(NOW - (ttl + 1) * HOUR): {"slug": "outside"},
    })
    live = se.pending_map(now=NOW)
    assert [v["slug"] for v in live.values()] == ["inside"]


def test_reading_never_rewrites_the_file(tmp_path, monkeypatch):
    p = write(tmp_path, monkeypatch, {str(NOW - 99 * HOUR): {"slug": "old"}})
    before = p.read_text(encoding="utf-8")
    se.pending_map(now=NOW)
    assert p.read_text(encoding="utf-8") == before


def test_pruning_is_what_shrinks_the_file(tmp_path, monkeypatch):
    p = write(tmp_path, monkeypatch, {
        str(NOW - 99 * HOUR): {"slug": "old"},
        str(NOW - 1 * HOUR): {"slug": "fresh"},
    })
    se.prune_pending(now=NOW)
    left = json.loads(p.read_text(encoding="utf-8"))
    assert [v["slug"] for v in left.values()] == ["fresh"]


def test_a_key_that_is_not_a_timestamp_is_left_alone(tmp_path, monkeypatch):
    # Never delete state we cannot reason about.
    write(tmp_path, monkeypatch, {"not-a-ts": {"slug": "mystery"}})
    assert se.pending_map(now=NOW) == {"not-a-ts": {"slug": "mystery"}}


def test_a_missing_file_is_simply_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "PENDING", tmp_path / "nope.json")
    assert se.pending_map(now=NOW) == {}
    se.prune_pending(now=NOW)          # must not raise
