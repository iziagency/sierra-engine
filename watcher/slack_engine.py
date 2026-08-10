r"""Sierra Engine — Slack Socket Mode listener that drives the cap-app pipeline.

A broker/OBA drops a file (photo of a handwritten app, a screenshot of an
email/text, a PDF) or pastes text into #app-intake. This listener:
  1. downloads any attached files,
  2. hands the message text + files to Claude headless running the cap-app
     skill, which extracts -> updates the client dossier -> fills the app,
  3. copies the outputs into the Sierra shared Drive (G:\Shared drives\Claude),
  4. replies in-thread with the Drive link, red flags, and what's missing.

Socket Mode = no public server, no port exposed. Runs on the hub machine.

Tokens (env or watcher/.env):
  SLACK_BOT_TOKEN   xoxb-...   (Bot User OAuth Token)
  SLACK_APP_TOKEN   xapp-...   (Socket Mode token)

Run:  python slack_engine.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox"          # where dropped files land before processing
DRIVE = Path(r"G:\Shared drives\Claude")
PROCESS = HERE / "process_drop.py"

# The ceiling has to sit above the sum of the stages inside it, or a heavy drop is
# killed mid-flight by arithmetic rather than by anything real:
#   extraction up to 330 (it scales with attachment count) + crops 130 + fill 40.
# Eight minutes is longer than a broker would wait in silence, which is exactly why
# the progress notices below exist — a visible wait is tolerable, an invisible one
# reads as a crash. A one-page drop still finishes in under three minutes.
PIPELINE_TIMEOUT = 500          # seconds
PROGRESS_EVERY = 120            # seconds between "still working" notes
# Every minute turned the thread into a drum roll; every two is enough to
# show the engine is alive without burying the answer under status lines.


def env_file_name() -> str:
    """Which .env to load: `--env <file>` wins, then SIERRA_ENV_FILE, then .env.

    The argument exists because `cmd /c "set SIERRA_ENV_FILE=… && start …"`
    silently fails to propagate the variable, and the engine then came up on the
    demo workspace while the operator believed it was on the client's. An
    argument is visible in the process list and cannot be half-applied.
    """
    argv = sys.argv[1:]
    if "--env" in argv:
        i = argv.index("--env")
        if i + 1 < len(argv):
            return argv[i + 1].strip()
    return os.environ.get("SIERRA_ENV_FILE", ".env").strip()


def load_env() -> None:
    # .env is the demo workspace, .env.sierra is Sierra Pacific's production one.
    # Separate files mean a switch can't half-apply — you never end up holding one
    # workspace's bot token and the other's app token.
    envf = HERE / env_file_name()
    if not envf.exists():
        print(f"[sierra-engine] no such env file: {envf}", file=sys.stderr)
        sys.exit(1)
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_env()
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

# Channels the engine may act in, by name or id. Empty means every channel it is
# a member of — fine for a private demo workspace, not fine in the client's, where
# being invited to the wrong channel would silently start filing real client work.
ALLOWED_CHANNELS = {
    c.strip().lstrip("#").lower()
    for c in os.environ.get("SLACK_ALLOWED_CHANNELS", "").split(",")
    if c.strip()
}

app = App(token=BOT_TOKEN)

_channel_names: dict[str, str] = {}


def channel_name(client, channel_id: str) -> str:
    """Channel id -> name, cached. Needs channels:read / groups:read."""
    if channel_id not in _channel_names:
        try:
            info = client.conversations_info(channel=channel_id)
            _channel_names[channel_id] = info["channel"].get("name", "")
        except Exception:  # noqa: BLE001 - scope may be missing; fall back to id only
            _channel_names[channel_id] = ""
    return _channel_names[channel_id]


def channel_allowed(client, channel_id: str) -> bool:
    if not ALLOWED_CHANNELS:
        return True
    if channel_id.lower() in ALLOWED_CHANNELS:
        return True
    return channel_name(client, channel_id).lower() in ALLOWED_CHANNELS


def download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BOT_TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())


# thread_ts -> client slug, so replies/corrections target the same client
THREADS = HERE / "threads.json"
# thread_ts -> judgment calls awaiting a yes/no from the broker
PENDING = HERE / "pending.json"

YES = {"yes", "y", "si", "sí", "ok", "okay", "correct", "correcto", "confirmed",
       "confirmado", "approved", "aprobado", "good", "bien", "perfect", "perfecto",
       "todo bien", "esta bien", "está bien", "yep", "yup", "asi es", "así es"}
NO = {"no", "nope", "wrong", "incorrect", "incorrecto", "mal", "esta mal",
      "está mal", "negative", "nel"}


# A confirmation nobody answered stops meaning anything. After a day the
# broker has moved on, and a "yes" typed into that old thread would sign off
# work they are no longer looking at — into a hash-chained log underwriters
# read. A Slack thread_ts IS a unix timestamp, so the age is in the key.
PENDING_TTL_HOURS = 24


def _pending_expired(thread_ts: str, now: float) -> bool:
    try:
        return (now - float(thread_ts)) > PENDING_TTL_HOURS * 3600
    except (TypeError, ValueError):
        return False        # not a timestamp we understand — never discard it


def pending_map(now: float | None = None) -> dict:
    """Live confirmations. Reading never rewrites the file — see prune_pending."""
    if not PENDING.exists():
        return {}
    raw = json.loads(PENDING.read_text(encoding="utf-8"))
    now = time.time() if now is None else now
    return {k: v for k, v in raw.items()
            if v and not _pending_expired(k, now)}


def prune_pending(now: float | None = None) -> int:
    """Drop expired confirmations from disk. Returns how many went. Startup only."""
    if not PENDING.exists():
        return 0
    raw = json.loads(PENDING.read_text(encoding="utf-8"))
    live = pending_map(now=now)
    if len(live) != len(raw):
        PENDING.write_text(json.dumps(live), encoding="utf-8")
    return len(raw) - len(live)


def set_pending(thread_ts: str, payload: dict | None) -> None:
    m = pending_map()
    if payload is None:
        m.pop(thread_ts, None)
    else:
        m[thread_ts] = payload
    PENDING.write_text(json.dumps(m), encoding="utf-8")


def verdict_of(text: str) -> str:
    """'yes' | 'no' | 'instruction' — deterministic, no model call needed."""
    t = re.sub(r"[^a-záéíóúñ ]", "", text.strip().lower()).strip()
    if t in YES or t in {f"{y} " for y in YES}:
        return "yes"
    if t in NO:
        return "no"
    first = t.split()[0] if t.split() else ""
    if first in YES and len(t.split()) <= 3:
        return "yes"
    if first in NO and len(t.split()) <= 3:
        return "no"
    return "instruction"


def record_verdict(slug: str, actor: str, ts: str, verdict: str, note: str = "") -> dict:
    payload = {"slug": slug, "actor": actor, "ts": ts,
               "verdict": verdict, "note": note}
    proc = subprocess.run(
        [sys.executable, str(PROCESS), "--confirm"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=180)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": proc.stderr[-300:] or "confirm failed"}


def thread_map() -> dict:
    if THREADS.exists():
        return json.loads(THREADS.read_text(encoding="utf-8"))
    return {}


def remember_thread(thread_ts: str, slug: str) -> None:
    m = thread_map()
    m[thread_ts] = slug
    THREADS.write_text(json.dumps(m), encoding="utf-8")


# The assemble commands. These do not read any new material; they compile what
# the client folder already holds, so they answer in seconds and can run inline.
#
# Three shapes have to be understood, because JC's team writes all three:
#
#   1. The written grammar — Task Assignment Coding Guide v1.0 (8.1.26),
#      "word order is mandatory", verb first:
#          TASK · SP NAME · POLICY · VENDOR (optional) · ARTIFACT
#          Prep SUMMI1 CAP app / Prep FALCO1 CAP AUW QP
#          Prep NORAS1 CAP RTS PROG Excel app
#   2. The verb-LAST form he dictated on the 7.29 call — his team heard it
#      there and will type it: NORAS1 CAP RTS PROG EXCEL APP PREP
#   3. Prose, in either language: "build the QP for FALCO1",
#      "arma el QP de lakeside".
#
_VERBS = (r"(?:arma|armar|haz|hacer|genera|generar|compila|compilar|prepara"
          r"|preparar|llena|llenar|build|make|assemble|create|prep|prepare"
          r"|fill|update|put\s+together)")
ASSEMBLE_VERB = re.compile(rf"\b{_VERBS}\b", re.I)
DOC_QP = re.compile(r"\bQP\b", re.I)
DOC_RTS = re.compile(r"\bRTS\b", re.I)
# In PROSE the verb must sit close, and BEFORE, the artifact it builds —
# "build the QP for FALCO1", never "update the carrier … because the QP shows
# Progressive", where verb and artifact belong to different clauses. Task-code
# lines don't need this: their whole vocabulary is already closed.
PROSE_QP = re.compile(rf"\b{_VERBS}\b.{{0,40}}\bQP\b", re.I | re.S)
PROSE_RTS = re.compile(rf"\b{_VERBS}\b.{{0,40}}\bRTS\b", re.I | re.S)

# Vocabulary of a task code line. A message made of nothing but these words
# (plus the SP code) is a command even with no verb; one word outside the list
# means it is prose, and prose needs a verb AND an artifact. That is what keeps
# "the FALCO1 CAP QP is missing the loss runs" from silently rebuilding a
# packet.
TASK_VERBS = {"PREP", "UPDATE"}
POLICIES = {"CAP", "WC", "GL", "GP", "EPLI"}
VENDORS = {"RTS", "PROG", "AUW", "AH", "WS", "KBK", "XPT", "TUMI", "CBI"}
# Vendors whose artifacts the engine can actually produce today. Everything
# else is parsed, acknowledged, and reported as not wired — "blank beats
# wrong": producing the Sierra version under a vendor's label is exactly the
# mislabelling the guide's reject rule exists to prevent.
WIRED_VENDORS = {"RTS", "PROG"}
TASK_WORDS = TASK_VERBS | POLICIES | VENDORS | {
    "QP", "EXCEL", "APP", "APPS", "SUPP", "SUPPL", "COMP", "NEW", "RENEWAL",
    "REN", "TOW", "TRUCKER", "GARAGE", "PROPERTY", "ACORD", "BP", "SP",
}
# In prose, only a code carrying its digit is unambiguous — "Desert" would
# otherwise read as a code. On a bare task line the code is found by position,
# so LAKES (no digit, verified in JC's own Drive) still works.
SP_IN_PROSE = re.compile(r"\b([A-Za-z]{4,6}\d)\b")


def parse_assemble(text: str, allow_app_prose: bool = False) -> dict | None:
    """Read a build command. None when the text is not one.

    Returns {'doc': 'qp'|'rts'|'app', 'qp', 'rts', 'sp', 'task', 'vendor',
    'unsupported_vendor', 'reject'}. `doc` is the primary artifact; RTS wins
    when both it and "app" are named, because "CAP RTS PROG Excel app" names
    the workbook, not the CAP app it feeds from.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    words = raw.replace("—", " ").split()
    upper = [w for w in (re.sub(r"[^A-Z0-9&]", "", x.upper()) for x in words) if w]

    # A task-code line: every word is grammar vocabulary except exactly one,
    # which is the SP name — first position in the written form, but accept it
    # anywhere so the dictated verb-last form parses identically.
    outsiders = [w for w in upper if w not in TASK_WORDS]
    bare_task = (len(upper) >= 2 and len(outsiders) == 1
                 and bool(re.fullmatch(r"[A-Z]{3,6}\d?", outsiders[0])))

    want_qp = bool(DOC_QP.search(raw))
    want_rts = bool(DOC_RTS.search(raw))
    want_app = ("APP" in upper or "APPS" in upper) and not want_qp
    if not (want_qp or want_rts or want_app):
        return None
    if not bare_task:
        # Prose: the verb must sit right before the artifact it builds — a
        # correction that happens to contain both words in different clauses
        # must never trigger a rebuild. And prose mentioning "app" is everyday
        # channel talk ("here's the app for Nora's"): only QP/RTS make prose a
        # command; a bare CAP-app build comes as a task-code line.
        want_qp = bool(PROSE_QP.search(raw))
        want_rts = bool(PROSE_RTS.search(raw))
        if not (want_qp or want_rts or (want_app and allow_app_prose)):
            return None

    sp = outsiders[0] if bare_task else ""
    if not sp:
        found = {m.group(1).upper() for m in SP_IN_PROSE.finditer(raw)
                 if m.group(1).upper() not in TASK_WORDS}
        sp = found.pop() if len(found) == 1 else ""

    vendor = next((w for w in upper if w in VENDORS and w != "RTS"), "")
    if vendor == "PROG":
        vendor = ""                      # RTS PROG is one vendor, the wired one
    task = "update" if "UPDATE" in upper else "prep"

    out = {"doc": "rts" if want_rts else ("qp" if want_qp else "app"),
           "qp": want_qp, "rts": want_rts, "sp": sp, "task": task,
           "vendor": vendor,
           "unsupported_vendor": bool(vendor and vendor not in WIRED_VENDORS),
           "reject": ""}
    # "Never label the Sierra version — FALCO1 CAP SP QP is a reject."
    if "SP" in upper and want_qp:
        out["reject"] = ("`SP` never labels the Sierra version — the vendor "
                         "slot alone changes it. For the Sierra packet: "
                         f"`Prep {sp or '<SP name>'} CAP QP`.")
    return out
# The short reply promises the full record on request, so the request has to work.
DETAIL_COMMAND = re.compile(r"^\s*(detalle|detail|todo|full)\b", re.I)


def try_detail_command(text: str, known_slug: str, say, thread_ts: str) -> bool:
    """Print everything the short reply left in the file. Returns True if handled.

    The message a broker gets is deliberately four lines; this is the other half
    of that bargain. Reading state.json is free — no model, no pipeline — so the
    detail is always one word away and never has to be pre-emptively dumped into
    the channel.
    """
    if not DETAIL_COMMAND.match(text or ""):
        return False
    sys.path.insert(0, str(HERE))
    from process_drop import find_client_in_text, CLIENTS
    slug = known_slug or find_client_in_text(text)
    if not slug or not (CLIENTS / slug / "state.json").exists():
        say(text=(":grey_question: Detail of which client? Name it, or reply in "
                  "its thread."), thread_ts=thread_ts)
        return True
    st = json.loads((CLIENTS / slug / "state.json").read_text(encoding="utf-8"))
    notes = st.get("_identifier_notes") or []
    log = st.get("_changelog") or []
    lines = [f":card_index_dividers: *{slug}* — full record"]
    if log:
        last = log[-1]
        lines.append(f"\n*Last entry* — {last['ts']} · {last['actor']} · "
                     f"{last['op']} · {len(last.get('changes') or [])} change(s)")
        for c in (last.get("changes") or [])[:15]:
            frm = "(blank)" if c.get("from") in (None, "") else c["from"]
            to = "(blank)" if c.get("to") in (None, "") else c["to"]
            lines.append(f"• `{c['field']}`: {frm} → {to}")
        if len(last.get("changes") or []) > 15:
            lines.append(f"_…and {len(last['changes']) - 15} more in CHANGELOG.md_")
    kept = st.get("_red_flags") or []
    if kept:
        lines.append(f"\n*Red flags on file ({len(kept)})*")
        lines += [f"• {f}" for f in kept]
    if notes:
        lines.append(f"\n*Reading notes ({len(notes)})*")
        lines += [f"• {n}" for n in notes]
    say(text="\n".join(lines), thread_ts=thread_ts)
    print(f"[detail] {slug}: {len(notes)} notes, {len(log)} log entries",
          flush=True)
    return True


def notion_stamp_line(slug: str, artifact: str) -> str:
    """The M6 stamp the guide says closes every build, ready to paste.

    Coding Guide v1.0: "When the build is done, stamp it in Notion in M6 form —
    FALCO1 CAP tow QP prep plus your initials — and close with a pass code:
    @ Broker Qs ans in + LRs in." The engine cannot write into Sierra's Notion
    (no integration token yet), so it hands the broker the exact line instead
    of leaving them to reconstruct it.
    """
    try:
        state = json.loads((HERE.parent / "app-form" / "clients" / slug /
                            "state.json").read_text(encoding="utf-8"))
        sp = state.get("sp_code") or slug
    except Exception:  # noqa: BLE001 - the stamp is a nicety, never a crash
        sp = slug
    return (f":spiral_note_pad: Notion stamp: `{sp} CAP tow {artifact} prep` "
            f"+ your initials · pass code `@ Broker Qs ans in + LRs in`")


# Allyssa's format, the one the person who assigns the work actually uses:
#
#     QP build> NORAS CAP, CARRS CAP, ONSIG2 CAP, BROOK2 CAP
#     RTS Prog excel app> SOUTH5 CAP, HAMIL CAP, NORAS CAP, ONSIG2 CAP
#
# Artifact BEFORE the verb, `>` introducing the list, and four clients on one
# line — none of which the guide's grammar or the dictated form allows.
BATCH_HEADER = re.compile(
    r"^\s*(?P<head>[^>\n]{0,40}?)\s*>\s*(?P<items>[^\n]+)$", re.M)
BATCH_ITEM_CODE = re.compile(r"^\s*([A-Z][A-Z0-9]{2,7})\b", re.I)


def _head_doc(head: str) -> str:
    """Which artifact a `... >` header names."""
    up = head.upper()
    if "RTS" in up or "PROG" in up or "EXCEL" in up:
        return "rts"
    if "QP" in up:
        return "qp"
    if "APP" in up:
        return "app"
    return ""


def parse_batch(text: str) -> list[dict]:
    """Every build this message asks for, in the order it asks for them.

    One assignment line can name four clients, and the order is part of the
    instruction — "Please do the Prog excel apps in that order first please."
    So the list comes back as written: never sorted, never deduped into
    something tidier than what was asked.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    jobs: list[dict] = []
    for m in BATCH_HEADER.finditer(raw):
        doc = _head_doc(m.group("head"))
        if not doc:
            continue
        for item in m.group("items").split(","):
            code = BATCH_ITEM_CODE.match(item.strip())
            if not code:
                continue
            jobs.append({"doc": doc, "qp": doc == "qp", "rts": doc == "rts",
                         "sp": code.group(1).upper(), "task": "prep",
                         "vendor": "RTS" if doc == "rts" else "",
                         "unsupported_vendor": False, "reject": ""})
    if jobs:
        return jobs

    # "prep ONSIG2 CAP SP app and RTS Prog excel app" — two documents, one
    # client, one sentence. Each is its own file and has to come back with its
    # own link, or the broker cannot tell which one landed. The client is named
    # once; the half after the "and" says what to build, not who for.
    # Only split a line that is ALREADY a command. "the app and the QP are both
    # missing loss runs" is a broker reporting a problem, and splitting it would
    # manufacture two builds out of a complaint.
    whole = parse_assemble(raw)
    segments = [s for s in re.split(r"\s+(?:and|y)\s+|\s*[&+]\s*", raw) if s.strip()]
    if whole and len(segments) > 1:
        # The verb is said once, at the front — "prep X and Y" — so the later
        # halves arrive verbless and would each read as prose. Carry it over.
        lead = ASSEMBLE_VERB.search(segments[0])
        verb = lead.group(0) if lead else "prep"
        parsed = [parse_assemble(s if ASSEMBLE_VERB.search(s) else f"{verb} {s}",
                                 allow_app_prose=True)
                  for s in segments]
        named = next((p["sp"] for p in parsed if p and p["sp"]), "")
        multi, seen = [], set()
        for p in parsed:
            if not p or p["doc"] in seen:
                continue
            seen.add(p["doc"])
            multi.append({**p, "sp": p["sp"] or named})
        if len(multi) > 1:
            return multi

    return [whole] if whole else []


def has_enough_to_build(dossier: dict) -> bool:
    """Is there enough on file to print an application at all?

    ONSIG2's adoption failed and left `{"sp_code": "ONSIG2"}` behind. The next
    command found that, called the client known, and printed a Sierra Pacific
    application with every field empty — a Drive link and a green check on a
    document worth nothing. A blank app is worse than an error because it
    looks like work.

    The minimum is a name for the risk. Vehicles without one are half a file:
    a hole to report, not a document to print.
    """
    c = (dossier or {}).get("company") or {}
    return bool(str(c.get("first_named_insured") or "").strip()
                or str(c.get("dba") or "").strip())


def discard_empty_shell(folder) -> None:
    """Remove a dossier folder we created and could not fill.

    Left on disk, the shell makes the client look known and sends the next
    attempt straight past the Drive lookup that would have rescued it. Only a
    folder holding nothing but that bare state.json is removed — anything with
    other work in it is left alone.
    """
    import shutil

    folder = Path(folder)
    state = folder / "state.json"
    if not state.exists():
        return
    if [p.name for p in folder.iterdir()] != ["state.json"]:
        return
    try:
        if has_enough_to_build(json.loads(state.read_text(encoding="utf-8"))):
            return
    except Exception:  # noqa: BLE001 - unreadable shell is still a shell
        pass
    shutil.rmtree(folder, ignore_errors=True)


def should_fall_back_to_reading(reason: str) -> bool:
    """Did the fast path decline because the packet carries no form at all?

    A flattened or scanned PDF has zero AcroForm fields — its answers are ink.
    That file is still the client's application and reads fine through the
    ordinary extraction. Any OTHER complaint (a partial page, a missing file)
    is not a reason to spend a model call re-deriving what the form already
    holds.
    """
    r = str(reason or "").lower()
    return "acroform" in r and ("0 page" in r or "does not look like" in r)


def _adopt_from_drive(sp_code: str, say, thread_ts: str) -> str:
    """Build a local file for a client we only know from Sierra's Drive.

    Reads their newest CAP packet — read-only, production is never written —
    and folds its AcroForm fields into a fresh dossier. Returns the slug, or
    "" and an explanation in the thread.
    """
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "reports"))
    import json as _json
    import re as _re

    import drive_pull
    import rts_fill

    got = drive_pull.pull_latest_packet(sp_code, INBOX / "_drive")
    if not got.get("ok"):
        say(text=f":grey_question: {got.get('error')}", thread_ts=thread_ts)
        return ""


    _, _, dba = got["folder"].partition(" ")
    slug = _re.sub(r"[^a-z0-9]+", "-", (dba or sp_code).lower()).strip("-")
    folder = rts_fill.CLIENTS / slug
    folder.mkdir(parents=True, exist_ok=True)
    state = folder / "state.json"
    if not state.exists():
        state.write_text(_json.dumps({"sp_code": sp_code.upper()}), encoding="utf-8")

    if got["mode"] == "materials":
        # No packet to read: their paperwork goes through the ordinary
        # extraction, which costs a model call and minutes rather than
        # seconds. Say so before the wait, not after.
        extra = (f" (+{got['dropped']} more not read — say so if they matter)"
                 if got.get("dropped") else "")
        say(text=(f":inbox_tray: *{got['folder']}* has no CAP packet — reading "
                  f"{len(got['files'])} piece(s) of their paperwork instead"
                  f"{extra}. This one takes a few minutes."),
            thread_ts=thread_ts)
        res = run_pipeline("", [Path(f) for f in got["files"]],
                           "engine (from Drive)",
                           __import__("datetime").datetime.now()
                           .strftime("%Y-%m-%d %H:%M"), slug)
        if not res.get("ok"):
            say(text=f":warning: {res.get('error')}", thread_ts=thread_ts)
            discard_empty_shell(folder)
            return ""
        say(text=format_reply(res), thread_ts=thread_ts)
        return res.get("slug") or slug

    try:
        warnings = rts_fill.apply_qp(slug, got["file"])
    except Exception as exc:  # noqa: BLE001
        if not should_fall_back_to_reading(str(exc)):
            say(text=(f":warning: read *{got['source_name']}* out of "
                      f"{got['folder']} but could not parse it: "
                      f"{type(exc).__name__}: {str(exc)[:150]}"),
                thread_ts=thread_ts)
            discard_empty_shell(folder)
            return ""
        # Flattened packet: the answers are ink, not form fields. Same paper,
        # slower road — and the client is not abandoned over a file format.
        say(text=(f":page_facing_up: *{got['source_name']}* is flattened — no "
                  f"form fields to read, so I'm reading the pages themselves. "
                  f"This takes a few minutes."), thread_ts=thread_ts)
        res = run_pipeline("", [Path(got["file"])] + [Path(f) for f in
                                                      (got.get("newer") or [])],
                           "engine (from Drive)",
                           __import__("datetime").datetime.now()
                           .strftime("%Y-%m-%d %H:%M"), slug)
        if not res.get("ok"):
            say(text=f":warning: {res.get('error')}", thread_ts=thread_ts)
            discard_empty_shell(folder)
            return ""
        say(text=format_reply(res), thread_ts=thread_ts)
        return res.get("slug") or slug

    data = _json.loads(state.read_text(encoding="utf-8"))
    lines = [f":inbox_tray: Adopted *{got['folder']}* from Drive — read "
             f"`{got['source_name']}`: "
             f"{len(data.get('vehicles') or [])} vehicle(s), "
             f"{len(data.get('drivers') or [])} driver(s)."]
    if warnings:
        lines.append(f"_{len(warnings)} reading note(s) — say `detail {sp_code}` "
                     f"to see them._")
    say(text="\n".join(lines), thread_ts=thread_ts)

    # The packet is the base, not the last word: anything in the folder dated
    # AFTER it is newer fact, and a loss run is gospel. An app built off the
    # packet alone would be stale the moment it was made.
    fresher = got.get("newer") or []
    if fresher:
        say(text=(f":new: {len(fresher)} document(s) in that folder are newer "
                  f"than the packet — reading them on top so the app carries "
                  f"the latest: " + ", ".join(f"`{Path(f).name}`" for f in fresher)),
            thread_ts=thread_ts)
        res = run_pipeline("", [Path(f) for f in fresher], "engine (from Drive)",
                           __import__("datetime").datetime.now()
                           .strftime("%Y-%m-%d %H:%M"), slug)
        if res.get("ok"):
            say(text=format_reply(res), thread_ts=thread_ts)
    return slug


def ask(subject: str, why: str = "") -> str:
    """A question with its subject in bold — what the broker has to go find out.

    A question buried in a sentence gets skimmed past. Bolding the subject is
    the difference between a message that gets acted on and one that gets a
    thumbs-up, and these are the fields a submission is priced from.
    """
    s = str(subject or "").strip().strip("*").strip()
    tail = str(why or "").strip()
    return f":grey_question: *{s}*" + (f" — {tail}" if tail else "")


def _as_question(line: str) -> str:
    """Turn an engine-written hole into a bold-subject question.

    The holes are already written as "<subject> — <why>", so the em dash is
    the seam.
    """
    subject, sep, why = str(line or "").partition("—")
    return ask(subject, why) if sep else ask(line)


def _dossier_of(slug: str) -> dict:
    try:
        return json.loads((HERE.parent / "app-form" / "clients" / slug /
                           "state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def open_questions_line(dossier: dict, show: int = 2) -> str:
    """What is still unresolved on this file, or '' when nothing is.

    A rebuild resolves nothing — whatever was open before it is open after.
    SUMMI1 was rebuilt with fourteen unanswered flags on file, including one
    asking whether the risk has any coverage in force, and the reply was a
    green check. A broker who sees a check mark sends the document.
    """
    flags = [f for f in (dossier.get("_red_flags") or []) if str(f).strip()]
    if not flags:
        return ""
    n = len(flags)
    head = (f":triangular_flag_on_post: *{n} question{'s' if n != 1 else ''} "
            f"still open on this file* — the rebuild did not answer them:")
    lines = [head] + [f"• {_short(f, 130)}" for f in flags[:show]]
    if n > show:
        lines.append(f"_+ {n - show} more — say `detail` to see them all._")
    return "\n".join(lines)


def rebuilt_app_reply(slug: str, out_name: str, link: str) -> str:
    """The full reply for a `Prep <SP> CAP app` rebuild."""
    parts = [f":white_check_mark: *{out_name}* rebuilt from the file."]
    if str(link).startswith("http"):
        parts.append(f":file_folder: <{link}|Open in Drive>")
    questions = open_questions_line(_dossier_of(slug))
    if questions:
        parts.append("")
        parts.append(questions)
    parts.append(notion_stamp_line(slug, "app"))
    return "\n".join(parts)


def try_assemble_command(text: str, known_slug: str, say, thread_ts: str) -> bool:
    """Handle a build command, or a whole assignment list. True if handled.

    An assignment names several clients at once and the order is part of the
    instruction, so a batch runs one at a time, in sequence, announcing where
    it is — a broker watching four builds needs to know which one is on.
    """
    jobs = parse_batch(text)
    if len(jobs) > 1:
        say(text=(f":clipboard: {len(jobs)} builds queued, in the order you "
                  f"listed them: "
                  + ", ".join(f"*{j['sp']}* {j['doc'].upper()}" for j in jobs)),
            thread_ts=thread_ts)
        for n, job in enumerate(jobs, 1):
            say(text=f":arrow_forward: {n}/{len(jobs)} — *{job['sp']}*",
                thread_ts=thread_ts)
            _run_one_build(job, "", say, thread_ts)
        return True

    cmd = jobs[0] if jobs else None
    if not cmd:
        return False
    return _run_one_build(cmd, known_slug, say, thread_ts, text)


def _run_one_build(cmd: dict, known_slug: str, say, thread_ts: str,
                   text: str = "") -> bool:
    """Carry out a single parsed build command."""
    want_qp, want_rts = cmd["qp"], cmd["rts"]
    want_app = cmd["doc"] == "app"

    # The guide's standing rules answer BEFORE any build starts. A reject is a
    # reject even when the client exists; an unwired vendor stops rather than
    # shipping the Sierra version under a vendor's label.
    if cmd.get("reject"):
        say(text=f":no_entry_sign: {cmd['reject']}", thread_ts=thread_ts)
        return True
    if cmd.get("unsupported_vendor"):
        say(text=(f":grey_question: *{cmd['vendor']}* isn't wired into the "
                  f"engine yet — today it builds the Sierra CAP app, the "
                  f"RTS/Progressive Excel and the Sierra QP. Building the "
                  f"Sierra version under a {cmd['vendor']} label would "
                  f"mislabel it, so nothing was made."), thread_ts=thread_ts)
        return True

    sys.path.insert(0, str(HERE))
    from process_drop import find_client_in_text, slug_for_sp_code
    # The SP code is the identifier his team actually uses, so it decides
    # before anything derived from a name does.
    # A named code IS the client, and it outranks the thread absolutely.
    # Chained with `or`, a code we had no local file for fell through to
    # `known_slug` — so `Prep CARRS CAP app` typed in Lakeside's thread put
    # Carrs Towing's application into Lakeside's folder, and the Drive lookup
    # that would have found CARRS never ran.
    if cmd["sp"]:
        slug = slug_for_sp_code(cmd["sp"])
        if not slug:
            say(text=f":mag: No file here for *{cmd['sp']}* — checking Drive…",
                thread_ts=thread_ts)
            slug = _adopt_from_drive(cmd["sp"], say, thread_ts)
    else:
        slug = known_slug or find_client_in_text(text)
    if not slug:
        unknown = f" I don't have a file under *{cmd['sp']}*." if cmd["sp"] else ""
        say(text=(f":grey_question: Happy to build it — but I don't know which "
                  f"client.{unknown} Name them, give me the SP code, or reply "
                  f"in their thread."),
            thread_ts=thread_ts)
        return True

    doc_name = {"qp": "QP", "rts": "RTS app", "app": "CAP app"}[cmd["doc"]]
    say(text=f":package: Building the {doc_name} for *{slug}*…",
        thread_ts=thread_ts)
    try:
        if cmd["doc"] == "app":
            # "Prep SUMMI1 CAP app": refill the Sierra application from the
            # dossier on file — no new material, no model call — and deliver.
            import datetime as _dt
            import subprocess as _sp
            from pathlib import Path as _P

            client_dir = HERE.parent / "app-form" / "clients" / slug
            state = json.loads((client_dir / "state.json").read_text(encoding="utf-8"))
            if not has_enough_to_build(state):
                say(text=(f":no_entry_sign: *{cmd['sp'] or slug}* has no data on "
                          f"file — not even the insured's name — so there is "
                          f"nothing to print. An empty application is worse "
                          f"than none. Send their paperwork, or say `detail "
                          f"{cmd['sp'] or slug}` to see what is missing."),
                    thread_ts=thread_ts)
                discard_empty_shell(client_dir)
                return True
            sp = state.get("sp_code") or slug
            client = (state.get("company") or {}).get("first_named_insured") or slug
            proc = _sp.run([sys.executable,
                            str(HERE.parent / "app-form" / "scripts" / "fill_app.py"),
                            "--client", client],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
            drafts = list(client_dir.glob("*_CAP_app_2026_DRAFT.pdf"))
            if proc.returncode != 0 or not drafts:
                say(text=f":warning: the fill failed: {(proc.stderr or proc.stdout)[-300:]}",
                    thread_ts=thread_ts)
                return True
            sys.path.insert(0, str(HERE))
            import formatting as _fmt
            from drive_api import client_folders, upload_to_drive
            out_name = f"{sp} CAP app {_fmt.format_date(_dt.date.today())}.pdf"
            _, fid, _ = client_folders(sp, client)
            link = upload_to_drive(str(drafts[0]), out_name, parent_id=fid)
            # Every build leaves a trace. Verifying this path meant reading
            # Drive timestamps because it printed nothing at all.
            print(f"[assemble] app {out_name} -> {str(link)[:110]}", flush=True)
            say(text=rebuilt_app_reply(slug, out_name, link), thread_ts=thread_ts)
            return True
        if want_qp:
            sys.path.insert(0, str(HERE.parent / "reports"))
            import qp_build
            r = qp_build.build(slug, "tow", to_drive=True)
            print(f"[assemble] {r.get('name')} pages={r.get('pages')} "
                  f"drive={str(r.get('drive'))[:150]}", flush=True)
            if not r.get("ok"):
                say(text=f":warning: {r.get('error')}", thread_ts=thread_ts)
                return True
            lines = [f":white_check_mark: *{r['name']}* — {r['pages']} pages — "
                     + ("*COMPLETE (comp)*" if r["complete"] else "incomplete, no comp suffix")]
            if r.get("gate"):
                lines.append("Still missing before it earns the comp suffix:")
                lines += [f"• {g}" for g in r["gate"]]
            if str(r.get("drive", "")).startswith("http"):
                lines.append(f":file_folder: <{r['drive']}|Open in Drive>")
            lines.append(notion_stamp_line(slug, "QP"))
            say(text="\n".join(lines), thread_ts=thread_ts)
        if want_rts:
            sys.path.insert(0, str(HERE.parent / "reports"))
            import rts_fill
            r = rts_fill.fill(slug)
            from drive_api import client_folders, upload_to_drive
            import json as _json
            state = _json.loads((HERE.parent / "app-form" / "clients" / slug /
                                 "state.json").read_text(encoding="utf-8"))
            c = state.get("company", {}) or {}
            sp = state.get("sp_code") or "CLIENT"
            _, fid, _ = client_folders(sp, c.get("first_named_insured") or slug)
            from pathlib import Path as _P
            link = upload_to_drive(r["file"], _P(r["file"]).name, parent_id=fid)
            print(f"[assemble] rts {_P(r['file']).name} cells={r['cells']} "
                  f"-> {str(link)[:110]}", flush=True)
            out = [f":white_check_mark: RTS app ready — {r['cells']} cells filled.",
                   f":file_folder: <{link}|Open in Drive>"]
            out += [f":rotating_light: *{m}*" for m in (r.get("dropped") or [])]
            out.append(notion_stamp_line(slug, "RTS Prog Excel app"))
            say(text="\n".join(out), thread_ts=thread_ts)
    except Exception as exc:  # noqa: BLE001
        say(text=f":warning: the build failed: {type(exc).__name__}: "
                 f"{str(exc)[:200]}", thread_ts=thread_ts)
    return True


def resolve_actor(client, user_id: str) -> str:
    """Slack user id -> real name (needs users:read; falls back to id)."""
    if not user_id:
        return "broker"
    try:
        info = client.users_info(user=user_id)["user"]
        return info.get("real_name") or info.get("name") or user_id
    except Exception:  # noqa: BLE001 - users:read optional
        return user_id


def run_pipeline(text: str, files: list[Path], actor: str, ts: str, slug: str,
                 progress=None) -> dict:
    """Run the drop. `progress(seconds)` is called about once a minute so the
    channel can be told the engine is alive; silence reads as a crash."""
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = {"text": text, "files": [str(f) for f in files],
               "actor": actor, "ts": ts, "client_slug": slug}
    if progress is None:
        try:
            proc = subprocess.run(
                [sys.executable, str(PROCESS)],
                input=json.dumps(payload),
                capture_output=True, text=True, timeout=PIPELINE_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return _timed_out(files)
        return _parse(proc)

    # With a progress callback the wait is done in slices so the channel hears
    # from us while the work is still going.
    proc_h = subprocess.Popen(
        [sys.executable, str(PROCESS)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")
    import threading
    box: dict = {}

    def pump():
        box["out"], box["err"] = proc_h.communicate(json.dumps(payload))

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    waited = 0
    while t.is_alive() and waited < PIPELINE_TIMEOUT:
        t.join(PROGRESS_EVERY)
        waited += PROGRESS_EVERY
        if t.is_alive() and waited < PIPELINE_TIMEOUT:
            try:
                progress(waited)
            except Exception:  # noqa: BLE001 - a failed notice must not kill the run
                pass
    if t.is_alive():
        proc_h.kill()
        t.join(10)
        return _timed_out(files)

    class _R:
        returncode = proc_h.returncode
        stdout = box.get("out") or ""
        stderr = box.get("err") or ""
    return _parse(_R())


def _timed_out(files: list[Path]) -> dict:
    # The worst failure mode is not failing — it is failing in silence. The
    # timeout used to escape the handler, Bolt logged the traceback, and the
    # channel saw an hourglass and then nothing, forever. Say what happened and
    # what to do about it.
    n = len(files)
    hint = (f" The drop carried {n} attachment(s); sending them in separate "
            f"messages usually gets through."
            if n > 1 else " Try again, or paste the key values as text.")
    return {"ok": False, "timed_out": True,
            "error": f"the pipeline ran past {PIPELINE_TIMEOUT // 60} minutes and was "
                     f"stopped, so nothing was saved.{hint}"}


def _parse(proc) -> dict:
    for ln in (proc.stderr or "").splitlines():
        if ln.startswith("[timing]"):
            print(ln, flush=True)          # lands in engine.log for later reading
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip()[-800:] or "pipeline failed"}
    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": f"unparseable output: {(proc.stdout or '')[-400:]}"}


OP_HEADER = {
    "create": ":sparkles: *{sp} — {client}* — new file created.",
    "add": ":heavy_plus_sign: *{sp} — {client}* — updated with new info.",
    "correct": ":pencil2: *{sp} — {client}* — correction applied.",
}


MAX_FLAGS = 4        # more than this and nobody reads any of them


def format_reply(res: dict) -> str:
    """The short version. Everything else is in the file, and the file is linked.

    This message used to run to fourteen numbered points, most of them the engine
    describing its own reading of the photographs. A broker between calls does not
    read fourteen points, so it amounted to saying nothing. Now: what happened,
    what blocks quoting, one question. The full record lives in state.json and
    CHANGELOG.md, and `detalle <client>` prints it on demand.
    """
    if not res.get("ok"):
        return (f":warning: Couldn't do it: {res.get('error', 'unknown error')}")

    ok = ":white_check_mark:"
    head = f"{ok} *{res['sp_name']} · {res['client']}* — {res.get('headline', '')}"
    lines = [head]

    link = res.get("folder_link", "")
    if link.startswith("http"):
        lines.append(f"<{link}|Open in Drive> · {res.get('filled_summary', '')}")
    else:
        lines.append(f"_{res.get('filled_summary', '')}_")

    # Phase 1 is two documents. Which ones came out is one line, always — a
    # broker must never have to infer from silence that only one was made.
    rts = res.get("rts") or {}
    if rts.get("delivered"):
        lines.append(f":page_facing_up: RTS/Progressive app filled — "
                     f"{rts.get('cells', 0)} cells.")
        # A unit that did not fit on the sheet is the loudest thing in the
        # message: the underwriter quotes what is on the paper.
        for missed in (rts.get("dropped") or []):
            lines.append(f":rotating_light: *{missed}*")
        # A hole is news; a deliberate blank is not. Only the holes get airtime.
        for blank in (rts.get("unknown_blanks") or [])[:3]:
            lines.append(_as_question(blank))
    elif rts.get("error"):
        lines.append(f":warning: RTS app failed — {rts['error']}")
    elif rts.get("unknown"):
        lines.append(f":grey_question: RTS undecided — {rts['reason']}")
    elif rts.get("reason"):
        lines.append(f":page_facing_up: SP app only — {rts['reason']}")

    flags = list(res.get("red_flags") or [])
    blockers = [m for m in (res.get("missing") or []) if m != "loss_runs"]
    if flags or blockers:
        lines.append("")
        shown = flags[:MAX_FLAGS]
        lines.append(f":triangular_flag_on_post: *Red flags ({len(flags)})*"
                     if flags else ":triangular_flag_on_post: *Red flags*")
        lines += [f"{i}. {_short(f)}" for i, f in enumerate(shown, 1)]
        tail = len(flags) - len(shown)
        extra = []
        if tail:
            extra.append(f"{tail} more")
        if res.get("filed_notes"):
            extra.append(f"{res['filed_notes']} reading note(s)")
        if extra:
            lines.append(f"_+ {' and '.join(extra)} in the file._")
        if blockers:
            lines.append(f":clipboard: Missing for the QP: {', '.join(blockers)}")
    else:
        lines.append("\n:white_check_mark: Nothing to flag.")

    if res.get("decisions"):
        lines.append(f"\n:mag: {_short(res['decisions'][0])}")
        if len(res["decisions"]) > 1:
            lines.append(f"_and {len(res['decisions']) - 1} more judgment "
                         f"call(s) — say `detalle` to see them._")
    lines.append("\nReply *yes* to sign off, or tell me what's wrong.")
    return "\n".join(lines)


# Every subtype a PERSON can produce by typing into a channel. An allow-list,
# because a skip-list gets outflanked: naming only ("bot_message", "channel_join")
# meant "<@U…> has left the channel" was fed to the extractor as client material.
# But the allow-list has to be complete in the other direction too — the first
# version held only (None, "file_share") and silently swallowed a broker's "yes"
# sign-off, because replying in a thread with "also send to channel" ticked
# arrives as "thread_broadcast". Nothing was logged; the approval just vanished.
HUMAN_SUBTYPES = (None, "file_share", "thread_broadcast", "me_message")


def is_human_message(event: dict) -> bool:
    """A message a person typed, as opposed to Slack narrating itself."""
    if event.get("bot_id"):
        return False
    return event.get("subtype") in HUMAN_SUBTYPES


HELP_TEXT = (
    "Here's what I can do:\n"
    "• *Start a file* — drop the email, photos of a handwritten app, or a PDF. "
    "I read it, fill the CAP app and file it in Drive.\n"
    "• *Update a file* — reply in the same thread: “add a 2022 Peterbilt, VIN …”, "
    "“the FEIN is 12-3456789”.\n"
    "• *Build the RTS app* — “make the RTS app”.\n"
    "• *Build the QP* — “build the QP”.\n"
    "• *See everything on file* — “detail”.\n"
    "I draft and flag; a broker still approves before anything is submitted."
)

# A client identifier is the strongest signal that a line of text is real material
# rather than conversation: USDOT / MC / CA number / FEIN.
CLIENT_ID_RE = re.compile(
    r"\b(?:us\s*dot|usdot|dot|mc|ca)\s*#?\s*\d{4,}\b|\b\d{2}-\d{7}\b", re.I)


def looks_like_client_material(text: str) -> bool:
    """Could this text plausibly carry client data worth opening a file for?

    Deliberately generous — a false negative only costs the broker a second
    message, while a false positive files a phantom client in the client's book.
    """
    t = text.strip()
    return bool(CLIENT_ID_RE.search(t)) or len(t) >= 200 or t.count("\n") >= 3


def _short(text: str, limit: int = 155) -> str:
    """One sentence, trimmed. The engine's reasoning belongs in the record, not
    in a paragraph a broker has to wade through to find the ask."""
    t = " ".join(str(text).split())
    for sep in (" — ", "; "):
        if len(t) > limit and sep in t:
            t = t.split(sep)[0]
    return t if len(t) <= limit else t[:limit - 1].rstrip(" ,.;:") + "…"


@app.event("message")
def handle_message(event, say, client):
    if not is_human_message(event):
        return
    # Silence outside the allowlist: no reply, no reaction, no pipeline. Answering
    # in a channel we were invited to by mistake would be worse than ignoring it.
    if not channel_allowed(client, event.get("channel", "")):
        return
    text = event.get("text", "") or ""
    thread_ts = event.get("thread_ts") or event.get("ts")
    files_meta = event.get("files", []) or []

    import datetime
    actor_early = resolve_actor(client, event.get("user", ""))
    now_human = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Assemble commands outrank everything: "arma el QP" typed into a thread that
    # still carried an old pending-confirmation was swallowed by the verdict block
    # below and fed to the extraction pipeline as if it were client material.
    text_early = (event.get("text") or "").strip()
    if not files_meta:
        known = thread_map().get(thread_ts, "")
        if try_assemble_command(text_early, known, say, thread_ts):
            return
        if try_detail_command(text_early, known, say, thread_ts):
            return

    # A confirmation is outstanding on this thread: a bare yes/no settles it.
    waiting = pending_map().get(thread_ts)
    if waiting and not files_meta:
        verdict = verdict_of(text)
        if verdict == "yes":
            out = record_verdict(waiting["slug"], actor_early, now_human, "approved")
            set_pending(thread_ts, None)
            say(text=(":white_check_mark: Locked in and signed off in the change history "
                      f"as approved by *{actor_early}*."), thread_ts=thread_ts)
            return
        if verdict == "no":
            record_verdict(waiting["slug"], actor_early, now_human, "rejected",
                           "broker says the change is wrong; correction pending")
            say(text=(":pencil2: Noted as *not* approved and logged. What should be "
                      "corrected? Tell me the right value (e.g. “the FEIN is 12-3456789” "
                      "or “keep the 2012 truck, don't replace it”) and I'll fix it."),
                thread_ts=thread_ts)
            return
        # anything longer is treated as the correction itself and falls through

    # nothing actionable
    if not files_meta and len(text.strip()) < 15:
        return

    known_thread = thread_map().get(thread_ts, "") or (waiting or {}).get("slug", "")
    if not files_meta and not known_thread and not looks_like_client_material(text):
        # A new client used to be conjured out of any sentence over 15 characters, so
        # "Hi, what can you do for me?" filed a real UNKNO1 "Unknown Client" folder in
        # Drive. Opening a client file is a write into the client's book — it needs
        # actual material behind it, not a greeting.
        say(text=HELP_TEXT, thread_ts=thread_ts)
        return

    try:
        client.reactions_add(channel=event["channel"], timestamp=event["ts"], name="eyes")
    except Exception:  # noqa: BLE001 - reactions:write scope optional, cosmetic only
        pass

    known_slug = thread_map().get(thread_ts, "") or (waiting or {}).get("slug", "")

    verb = "Applying your update" if known_slug else "Reading it and building the app"
    say(text=f":hourglass_flowing_sand: {verb}…", thread_ts=thread_ts)

    actor, ts_human = actor_early, now_human

    INBOX.mkdir(parents=True, exist_ok=True)
    local_files = []
    for fm in files_meta:
        url = fm.get("url_private_download") or fm.get("url_private")
        if not url:
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", fm.get("name", "drop"))
        dest = INBOX / f"{event['ts']}_{safe}"
        try:
            download_file(url, dest)
            local_files.append(dest)
        except Exception as exc:  # noqa: BLE001
            say(text=f":warning: couldn't download {fm.get('name')}: {exc}", thread_ts=thread_ts)

    def note(seconds: int) -> None:
        mins = seconds // 60
        left = (PIPELINE_TIMEOUT - seconds) // 60
        say(text=(f":gear: Still on it — {mins} min in"
                  + (f", up to {left} more before I stop and report back."
                     if left else ".")), thread_ts=thread_ts)

    res = run_pipeline(text, local_files, actor, ts_human, known_slug, progress=note)
    if res.get("ok"):
        # the drop is archived in the client's _source and in Drive; keeping the
        # inbox copy too just grows a third pile of the same bytes forever
        for f in local_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    if res.get("ok") and res.get("slug"):
        remember_thread(thread_ts, res["slug"])
        set_pending(thread_ts,
                    {"slug": res["slug"]} if res.get("decisions") else None)
    say(text=format_reply(res), thread_ts=thread_ts)


def pid_is_live_engine(pid: int) -> bool:
    """Is `pid` a running copy of this engine — or just a number in a stale file?

    engine.pid outlives a power cut and a reboot, and by the time the machine is
    back the number in it usually belongs to something else entirely. So neither
    branch settles for "a process with that id exists": both ask the OS what the
    process actually is.

    os.kill(pid, 0) — the usual portable trick — is deliberately not used.
    CPython maps os.kill on Windows onto TerminateProcess, so asking a process
    whether it is alive would kill it.
    """
    argv = (["ps", "-p", str(pid), "-o", "command="] if os.name != "nt"
            else ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    try:
        out = subprocess.run(argv, capture_output=True, text=True).stdout or ""
    except OSError as exc:
        # No probe, no evidence. Refusing to boot here would turn a missing
        # binary into a silent outage that launchd retries forever, so start
        # and say so loudly instead.
        print(f"[sierra-engine] cannot check PID {pid} ({exc}); assuming it is dead",
              file=sys.stderr)
        return False
    # `ps` prints the command line, `tasklist` prints only the image name, so
    # each platform is asked the most specific question it can answer.
    needle = "slack_engine" if os.name != "nt" else "python"
    return needle in out.lower()


def claim_single_instance() -> None:
    """Refuse to start if another engine is already connected.

    Two Socket Mode connections on the same app make Slack deliver each event to
    only ONE of them, so half the messages hit whichever instance is running
    older code and vanish silently. Hard to spot, so make it impossible.
    """
    lock = HERE / "engine.pid"
    if lock.exists():
        try:
            old = int(lock.read_text(encoding="utf-8").strip())
        except ValueError:
            old = None
        if old and old != os.getpid() and pid_is_live_engine(old):
            stop = f"taskkill /PID {old} /F" if os.name == "nt" else f"kill {old}"
            print(f"[sierra-engine] another engine is already running (PID {old}). "
                  f"Stop it first: {stop}", file=sys.stderr)
            sys.exit(1)
    lock.write_text(str(os.getpid()), encoding="utf-8")


if __name__ == "__main__":
    env_file = env_file_name()
    missing = [n for n, v in (("SLACK_BOT_TOKEN", BOT_TOKEN), ("SLACK_APP_TOKEN", APP_TOKEN)) if not v]
    if missing:
        print(f"Missing tokens: {', '.join(missing)} — set them in watcher/{env_file}",
              file=sys.stderr)
        sys.exit(1)
    claim_single_instance()
    # Which workspace this instance is bound to is the first thing to check when
    # something lands in the wrong place, so say it out loud at startup.
    try:
        who = app.client.auth_test()
        where = f"{who.get('team')} ({who.get('team_id')})"
    except Exception as exc:  # noqa: BLE001 - a bad token should say so, not crash silently
        print(f"[sierra-engine] token rejected by Slack: {exc}", file=sys.stderr)
        sys.exit(1)
    dropped = prune_pending()
    if dropped:
        print(f"[sierra-engine] dropped {dropped} confirmation(s) older than "
              f"{PENDING_TTL_HOURS}h — a stale yes must not sign anything off",
              file=sys.stderr)
    scope = ", ".join(sorted(ALLOWED_CHANNELS)) or "ALL CHANNELS (no allowlist set)"
    print(f"[sierra-engine] listening · workspace={where} · env={env_file} · "
          f"channels={scope} · drive={DRIVE}", file=sys.stderr)
    SocketModeHandler(app, APP_TOKEN).start()
