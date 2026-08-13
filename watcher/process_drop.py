"""Process one Slack drop: create / add / correct a client's CAP app, with a
full audit trail, and deliver an organized folder to Drive.

Input JSON on stdin:
  {"text": "...", "files": ["path", ...], "actor": "Rafael Chacon",
   "ts": "2026-07-23 09:14", "client_slug": "<known slug or empty>"}

Output: one JSON line the Slack listener formats.

Flows (auto-detected):
  CREATE  — no dossier yet: extract from the drop, build the app.
  ADD     — dossier exists + new material: merge, keep the same file.
  CORRECT — dossier exists + a text instruction ("FEIN should be X"): apply it.

Every run appends a changelog entry (who / when / what / how) and re-renders
CHANGELOG.md into the client's Drive folder. Source materials (the email
screenshot, the SMS, the handwritten scan, the broker's text) are archived
into the client's _source subfolder.
"""
from __future__ import annotations

import datetime
import hashlib
import time
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import formatting  # noqa: E402 - needs sys.path set up first
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reports"))
import qp_read  # noqa: E402 - needs sys.path set up first; QP PDF -> dossier fast path

GENESIS = "0" * 16  # first-link anchor for the tamper-evident chain

# Every budget in this file was first set by arithmetic and every one of them was
# wrong — extraction of six images turned out to cost 158s where 120 was allowed
# and 300 was assumed, while the identifier crop pass costs 7.6s PER FIELD, which
# is where the time actually goes. So the pipeline now reports its own timings to
# stderr; the next slow run explains itself instead of being reasoned about.
_T0 = time.monotonic()
_STAGES: list[tuple[str, float]] = []


def stage(name: str) -> None:
    now = time.monotonic()
    prev = _STAGES[-1][1] if _STAGES else _T0
    _STAGES.append((name, now))
    print(f"[timing] {name}: {now - prev:.0f}s (total {now - _T0:.0f}s)",
          file=sys.stderr, flush=True)


def entry_fingerprint(entry: dict, prev_hash: str) -> str:
    """Hash-chain link: sha256 over (prev_hash + canonical entry sans hash).
    Any later edit/delete breaks every downstream fingerprint -> detectable."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "prev")}
    payload = prev_hash + json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

# Derived from this file, never written down. Held as an absolute path to one
# machine, the engine ran only in that directory: an install that followed the
# guide and cloned to C:\sierra-pacific failed on every drop with ENOENT on
# app-form/config/client_data.example.json.
ROOT = Path(__file__).resolve().parent.parent
APPFORM = ROOT / "app-form"
FILL = APPFORM / "scripts" / "fill_app.py"
SCHEMA = APPFORM / "config" / "client_data.example.json"
CLIENTS = APPFORM / "clients"

# Budget, in seconds, inside the 300s ceiling slack_engine enforces:
#   extraction 120  +  identifier crops 130  +  fill and Drive ~40  =  290
# The old 420 for extraction alone was longer than the whole ceiling, which is how
# a six-image email consumed the run and was lost with nothing saved.
EXTRACT_TIMEOUT = 120           # one or two attachments, the common case


def extract_budget(n_files: int, files: list | None = None) -> int:
    """Seconds to allow the extractor, given how much it has to LOOK AT.

    Counting attachments was wrong twice: six photos in one email starved, and
    then a 32-page renewal QP — one attachment — got the one-file budget of 120s
    while actually costing ~125s. It cleared the CLI test by five seconds of luck
    and died inside the engine. The unit of work is pages seen, not files sent,
    so PDFs weigh in by their page count.
    """
    units = n_files
    for f in files or []:
        if str(f).lower().endswith(".pdf"):
            try:
                import fitz
                with fitz.open(f) as d:
                    units += max(0, (len(d) - 1) // 3)   # every 3 extra pages ≈ 1 image
            except Exception:  # noqa: BLE001
                units += 2                               # unreadable: assume heavy
    return min(120 + 45 * max(0, units - 2), 420)


def claude_bin() -> str:
    for name in ("claude.cmd", "claude.exe", "claude"):
        f = shutil.which(name)
        if f:
            return f
    win = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
    return str(win) if win.exists() else "claude"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


BOOK_CACHE = ROOT / "watcher" / "client_book.json"
BOOK_TTL_HOURS = 12


def client_book(refresh: bool = False) -> list[str]:
    """Every client folder name in JC's Prospects and Clients drives.

    Read-only, and cached: it is ~4,000 folders and a code has to be resolved
    on every drop. Stale-but-present beats absent — a code resolved against
    yesterday's list is right for every client that already existed, while an
    empty list makes `spcode.resolve` flag its answer as a guess.
    """
    if not refresh and BOOK_CACHE.exists():
        try:
            age = time.time() - BOOK_CACHE.stat().st_mtime
            cached = json.loads(BOOK_CACHE.read_text(encoding="utf-8"))
            if age < BOOK_TTL_HOURS * 3600 and cached:
                return cached
        except Exception:  # noqa: BLE001 - a bad cache is refetched, never fatal
            pass
    try:
        from drive_api import (CLIENTS_ROOT, PROSPECTS_ROOT, GoogleDriveGateway,
                               list_all_identity_folders, service)
        gw = GoogleDriveGateway(service())
        names = sorted({m.name for m in
                        list_all_identity_folders(gw, [PROSPECTS_ROOT, CLIENTS_ROOT])})
        if names:
            BOOK_CACHE.write_text(json.dumps(names), encoding="utf-8")
        return names
    except Exception:  # noqa: BLE001 - offline: fall back to whatever is cached
        if BOOK_CACHE.exists():
            try:
                return json.loads(BOOK_CACHE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return []
        return []


def sp_name(dba: str, book: list[str] | None = None) -> str:
    """The client's SP code — read from JC's book, not computed.

    This used to be five letters plus "1". Against the real 3,975 folders that
    put Brookfield under BROOK1 when Sierra files it as BROOK2, Nora's under
    NORAS1 when it is NORAS — and worse, FALCO1/RIDGE1/SHORE1 are Desert Valley
    Towing, Ridge Route Towing and Coastal Towing, so an invented code filed one
    insured's paperwork into another's. See shared/spcode.py.
    """
    import spcode
    return spcode.resolve(dba, book if book is not None else client_book()).code


def slug_for_sp_code(sp: str) -> str:
    """Existing local dossier folder carrying this SP code, oldest first.

    The folder name comes from the company name, which varies between readings
    of the same paperwork; the SP code does not. Preferring the oldest match
    means that where a fork already happened, work continues in the original
    rather than the split moving somewhere new.
    """
    if not sp:
        return ""
    hits = []
    for state in CLIENTS.glob("*/state.json"):
        try:
            if json.loads(state.read_text(encoding="utf-8")).get("sp_code") == sp:
                hits.append((state.stat().st_mtime, state.parent.name))
        except Exception:  # noqa: BLE001 - a broken dossier must not break the lookup
            continue
    return min(hits)[1] if hits else ""


# ------------------------------------------------------------------ Claude

def claude_run(prompt: str, timeout: int | None = None) -> str:
    cmd = [claude_bin(), "-p", "--output-format", "text",
           "--allowedTools", "Read", "--model", "claude-sonnet-5"]
    # Same cp1252 trap as the fill_app call below: the extractor returns client
    # names, addresses and quoted text, so a non-Latin-1 character is a matter of
    # time, not chance.
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=timeout or EXTRACT_TIMEOUT, shell=False,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        # The CLI prints API errors ("API Error: 529 Overloaded") on STDOUT, not
        # stderr. Reading only stderr threw the real cause away and left the
        # broker with a guessed explanation about file sizes.
        detail = (proc.stderr.strip() or proc.stdout.strip())[-400:]
        raise RuntimeError(f"claude failed: {detail}")
    return proc.stdout.strip()


def extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise RuntimeError(f"no json in claude output: {raw[:300]}")
    return json.loads(m.group(0))


CREATE_PROMPT = """You are the extraction stage of the Sierra Pacific cap-app skill.
Read the attached image(s) and/or the text below (a photo of a handwritten CAP
application, a screenshot of a client email/text, a call note, or pasted text)
and produce ONE json object of client data.

Rules (non-negotiable):
- Do NOT crop, zoom, rotate or otherwise process the images, and do not write any
  files. Read each attachment once and report what you can see. A separate pass
  re-reads every identifier from a magnified crop of its own cell using the
  form's exact coordinates, so investigating pixels here is duplicated work that
  costs minutes — and a broker waiting in Slack reads a long pause as a crash.
- Where a number is not clearly legible, put null. Do not guess and do not go
  looking; the crop pass will get it.
- NEVER invent data. If a field isn't stated, omit the key. Blank beats wrong.
- Follow this schema exactly: {schema}
- Include the client's name so we can identify the file.
- In a "red_flags" array, note anything a tow-fleet underwriter would question:
  vehicle count vs list mismatch, undisclosed accidents ("nothing filed",
  "fixed it ourselves"), stated value bundling equipment ("with the winch"),
  name variants, address mismatches, ops-vs-web conflicts, percentages that
  don't total 100.
- Add "_op": "create" and "_summary": one short line describing the intake.

Output ONLY the json object, no prose, no fences.

CLIENT MESSAGE TEXT:
{text}
"""

UPDATE_PROMPT = """You are the update stage of the Sierra Pacific cap-app skill.
Here is the client's CURRENT dossier (json):
{dossier}

The broker just sent this (text and/or attached image):
{text}

Decide which this is:
- "add": new information to merge into the app (more trucks, drivers, VINs, a
  form the client filled, an SMS with data).
- "correct": an instruction to FIX an existing value ("the FEIN is wrong, it's
  X", "truck 1 value should be 105k", "change the carrier to Y").

Do NOT crop, zoom, rotate or process the images, and do not write files. Read
each attachment once. Identifiers are re-read separately from magnified crops, so
put null where a number is unclear rather than investigating it.

Return ONE json object = the FULL updated dossier (same schema), with the new
info merged (for "add") or the wrong value overwritten (for "correct").
Match vehicles by vin, drivers by name, loss runs by year.
NEVER invent data. Keep everything already correct.

Do BOTH of these in the same pass:
1. Carry out the broker's explicit instruction exactly.
2. Additionally fill any field that is currently blank/missing whenever the new
   material clearly states it (FEIN, CA #, phone, addresses, driver details...).
   Never overwrite an existing value this way — blanks only. Values the broker
   explicitly corrects are the exception and do get overwritten.
Add "_op": "add" or "correct", and "_summary": one short line of what you did.
Also keep a "red_flags" array if anything new is worth flagging.

Output ONLY the json object, no prose, no fences.
"""


# Fields whose value carries no redundancy: no linguistic pattern to fall back on,
# no checksum the reader can verify, every digit equally likely a priori. A vision
# model reconstructs the predictable parts of a string from its priors and has
# nothing to lean on for the rest — on the Borderline Recovery test it returned the
# FEIN as 83-3766771 for a legible 82-5566771, keeping the 2-7 shape and the final
# 6771 while inventing the middle, and turned 760-555-0193 into 760-555-0543 with
# the patterned 760-555 prefix intact. Format validation cannot catch either: both
# EIN prefixes are real and 760 is a real area code.
#
# So these are read a second time, independently, and only written when the two
# readings agree. A disagreement leaves the field BLANK for a human, because JC's
# rule is "blank beats wrong" and a plausible-but-wrong tax ID reaches the
# underwriter, gets the submission rejected, and nobody catches it.
IDENTIFIER_PATHS = (
    "company.fein", "company.usdot", "company.mc_number",
    "company.state_filing_number", "company.contact_cell", "company.office_phone",
    "company.owner_cell", "vehicles[].vin", "drivers[].license_number",
    "drivers[].birthday",
)

VERIFY_PROMPT = """You are the verification stage of the Sierra Pacific cap-app skill.

Transcribe ONLY the identifier fields listed below from the attached file(s).
Work digit by digit. Do not infer, do not complete a pattern, do not use what a
number "should" look like. If a character is not clearly legible, put null for
that whole field rather than a best guess.

Return ONE json object, no prose:
{{
 "company": {{"fein": "...", "usdot": "...", "mc_number": "...",
              "state_filing_number": "...", "contact_cell": "...",
              "office_phone": "...", "owner_cell": "..."}},
 "vehicles": [{{"vin": "..."}}],
 "drivers": [{{"license_number": "...", "birthday": "..."}}]
}}

Use null for anything not present in the source. Keep vehicles and drivers in the
same order they appear in the document. Text also provided, if any:
{text}
"""


def _norm_id(v) -> str:
    """Compare identifiers on their characters, not their punctuation.

    `82-5566771` and `82.5566771` are the same tax ID written two ways; JC writes
    separators as dots and clients write them as dashes. Only a genuine character
    difference should block a field.
    """
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def m8_date(v) -> str | None:
    """`08/09/1981` and `8.9.81` -> `8.9.81`, JC's format. None if not a date.

    Both spellings are the same day, so comparing them as characters made the
    verifier blank a correct date of birth on Brookfield's real file. A date
    has to be compared as a date; stripping punctuation turns 08091981 and 8981
    into a disagreement that does not exist. Two-digit years resolve to 1900s
    when they would otherwise land in the future — nobody on a driver schedule
    was born next year.

    Thin wrapper: the parsing/formatting itself lives in shared/formatting.py
    (JC's 7.27 call consolidated four separate copies of this into one module —
    this one, lossruns.m8, rts_fill.m8 and qp_build.m8 all delegate to it now).
    """
    return formatting.format_date(v)


def ids_agree(a, b) -> tuple[bool, object]:
    """Do two readings mean the same thing, and what should be written?

    Returns (agree, value). Dates agree when they are the same day whatever the
    spelling, and the value written is JC's `M.D.YY`.
    """
    da, db = m8_date(a), m8_date(b)
    if da and db:
        return da == db, da
    na, nb = _norm_id(a), _norm_id(b)
    if na == nb:
        return True, a
    # One reading kept the state prefix and the other did not: "CA 0489217" and
    # "0489217" are one filing number, and blanking it cost the identifier JC's
    # file matching actually runs on — Brookfield was found by its CA number.
    # The bare number is what the form wants, so that is what gets written.
    sa, sb = _strip_state_prefix(na), _strip_state_prefix(nb)
    if sa == sb and sa:
        return True, sa
    return False, a


def _strip_state_prefix(norm: str) -> str:
    """`CA0489217` -> `0489217`. Only when what follows is all digits, so a
    genuine alphanumeric ID like `A1B2C3` is left alone."""
    m = re.match(r"^[A-Z]{2}(\d{4,})$", norm)
    return m.group(1) if m else norm


def verify_identifiers(data: dict, text: str, files: list[str]) -> list[str]:
    """Second independent read of the identifier fields.

    Mutates `data`: agreeing values stay, disagreeing ones are blanked. Returns a
    human-readable note per field that needs typing in by hand.
    """
    if not files:
        return []                      # nothing to re-read; typed text is not a misread
    # The same holds when the typing arrived as a file. A .txt has one possible
    # reading, so a second pass can only introduce variance: on Lakeside's typed
    # email it came back "illegible" for two dates of birth that are spelled out
    # in the document, and blanked them both. Re-reading protects against
    # handwriting, not against text.
    if all(Path(f).suffix.lower() in TYPED_SUFFIXES for f in files):
        return []
    prompt = VERIFY_PROMPT.format(text=text or "(no text, see image)")
    prompt += "\n\nATTACHED FILES (read each):\n" + "\n".join(files)
    try:
        second = extract_json(claude_run(prompt))
    except Exception as exc:  # noqa: BLE001
        return [f"identifier re-read failed ({type(exc).__name__}); the numbers below "
                f"come from a single unverified reading: "
                f"{', '.join(sorted(_present_ids(data)))}"]

    notes: list[str] = []
    c1, c2 = data.get("company") or {}, second.get("company") or {}
    for key in ("fein", "usdot", "mc_number", "state_filing_number",
                "contact_cell", "office_phone", "owner_cell"):
        a, b = c1.get(key), c2.get(key)
        if not a:
            continue
        agree, value = ids_agree(a, b)
        if agree:
            c1[key] = value          # normalised spelling, e.g. a date as M.D.YY
        else:
            c1[key] = None
            notes.append(f"company.{key}: first reading “{a}”, second reading "
                         f"“{b or 'illegible'}” — left BLANK, type it in by hand")

    for listname, keys in (("vehicles", ("vin",)),
                           ("drivers", ("license_number", "birthday"))):
        rows1, rows2 = data.get(listname) or [], second.get(listname) or []
        # Rows are paired by their own key, not by their place in the list. The
        # two readings do not have to agree on ORDER, and when they did not, the
        # comparison ran Salomon's date of birth against Hector's, called both a
        # disagreement, and blanked two dates that were each read correctly twice.
        ident = LIST_KEYS.get(listname, "")
        by_key = {_row_key(listname, r.get(ident)): r for r in rows2
                  if isinstance(r, dict) and r.get(ident)}
        for i, row in enumerate(rows1):
            if not isinstance(row, dict):
                continue
            mine = _row_key(listname, row.get(ident))
            other = by_key.get(mine) if mine else None
            if other is None and mine and listname == "drivers":
                near = next((k for k in by_key if _one_letter_apart(mine, k)), None)
                other = by_key.get(near) if near else None
            if other is None and not mine:
                # no key to pair on: fall back to position, which is all there is
                other = rows2[i] if i < len(rows2) and isinstance(rows2[i], dict) else {}
            other = other or {}
            for key in keys:
                a, b = row.get(key), other.get(key)
                if not a:
                    continue
                agree, value = ids_agree(a, b)
                if agree:
                    row[key] = value
                else:
                    row[key] = None
                    notes.append(f"{listname}[{i + 1}].{key}: first reading “{a}”, "
                                 f"second reading “{b or 'illegible'}” — left BLANK, "
                                 f"type it in by hand")
    return notes


def _present_ids(data: dict) -> set[str]:
    c = data.get("company") or {}
    return {f"company.{k}" for k, v in c.items()
            if v and k in ("fein", "usdot", "mc_number", "state_filing_number",
                           "contact_cell", "office_phone", "owner_cell")}


def expand_emails(files: list[str], scratch: Path) -> tuple[list[str], str, list[str]]:
    """Turn any .eml into its text body plus real image files on disk.

    A forwarded email is a MIME envelope: Brookfield's quote is 931 KB where
    the six photographs are base64 inside the text. Handed the raw .eml, a reader
    sees only that base64 and cannot look at the pictures — the only way it ever
    saw them was by decoding the file itself, which is slow, unbounded, and not
    something a model should be doing at all. Decoding MIME is a solved problem in
    the standard library, so it happens here, before any model is involved. The
    images then travel the same path as any other attachment, crop pass included.

    Returns (files with .eml replaced by its images, text harvested, notes).
    """
    import email
    from email import policy

    out: list[str] = []
    harvested: list[str] = []
    notes: list[str] = []
    for f in files:
        p = Path(f)
        if p.suffix.lower() not in (".eml", ".msg"):
            out.append(f)
            continue
        try:
            msg = email.message_from_bytes(p.read_bytes(), policy=policy.default)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{p.name}: could not be parsed as an email ({exc}); "
                         f"passed through unchanged")
            out.append(f)
            continue

        dest = scratch / f"{p.stem}_parts"
        dest.mkdir(parents=True, exist_ok=True)
        hdr = " | ".join(filter(None, [
            f"From: {msg.get('From', '')}", f"To: {msg.get('To', '')}",
            f"Subject: {msg.get('Subject', '')}", f"Date: {msg.get('Date', '')}"]))
        harvested.append(f"[email] {hdr}")

        n = 0
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype == "text/plain":
                try:
                    harvested.append(part.get_content().strip())
                except Exception:  # noqa: BLE001
                    pass
            elif ctype.startswith("image/"):
                data = part.get_payload(decode=True)
                if not data:
                    continue
                n += 1
                ext = {"image/png": ".png", "image/jpeg": ".jpg",
                       "image/webp": ".webp"}.get(ctype, ".png")
                q = dest / f"{p.stem}_img{n}{ext}"
                q.write_bytes(data)
                out.append(str(q))
            elif ctype == "application/pdf":
                data = part.get_payload(decode=True)
                if data:
                    n += 1
                    q = dest / f"{p.stem}_att{n}.pdf"
                    q.write_bytes(data)
                    out.append(str(q))
        notes.append(f"{p.name}: unpacked {n} attachment(s) from the email")
    return out, "\n".join(h for h in harvested if h), notes


# Words that turn a drop from "here is more material" into "change what you have".
# JC's real forwarded quote reads "New info. Replace other truck." — the
# deterministic merge below fills blanks and never overwrites, which is right for
# an addition and exactly wrong for that sentence: it would leave the old truck on
# the policy and add a second one. An instruction has to be interpreted, so it
# goes to the model instead of the merge.
# Both languages: the application has a preferred-language field with Spanish on
# it, so instructions will arrive in Spanish too.
INSTRUCTION_WORDS = re.compile(
    r"(\b(replace|replaces|replacing|remove|removed|delete|drop|swap|instead of|"
    r"no longer|not anymore|change|changed|correct|corrected|fix|should be|"
    r"is wrong|isn'?t right|update the|"
    r"reemplaz\w*|quita\w*|quítal\w*|borra\w*|cambia\w*|corrige|corregir|"
    r"en lugar de|ya no|debe ser|equivocad\w*|incorrect\w*)\b"
    r"|\bdeber[íi]a ser\b|\best[áa]\s+mal\b|\bno\s+es\s+\w+,?\s*es\b)", re.I)


# "the FEIN is 12-3456789" — the phrasing the engine itself recommends when a
# broker rejects a change, and which used to fall through to the update path.
# Deliberately narrow: a SHORT message stating one field and a value that
# contains a digit. A false positive here is expensive — material read as a
# correction loses everything the message carried beyond the named field — so
# a full intake (long, multi-line) and a value with no number in it both stay
# on the material path.
VALUE_STATEMENT_MAX = 90
VALUE_STATEMENT = re.compile(
    # subject: a field name, never a pronoun — "this is client number 3 of the
    # day and the rest is attached" is a note about a drop, not a correction
    r"^\s*(?:the|el|la|los|las)?\s*"
    r"(?!this\b|that\b|it\b|esto\b|eso\b|esta\b|este\b|aqui\b|here\b)"
    r"[\w#/&'.\- ]{2,40}?\s+(?:is|es|son|are)\s+"
    # value: SHORT and carrying a number. A correction names one value briefly;
    # anything that runs on is prose about the drop.
    r"(?=[^.\n]{0,30}$)[^.\n]*\d[^.\n]*$", re.I)


def carries_instruction(text: str) -> str:
    """The instruction phrase found in the drop, or '' if it reads as an addition."""
    m = INSTRUCTION_WORDS.search(text or "")
    if m:
        return m.group(0)
    s = (text or "").strip()
    if len(s) <= VALUE_STATEMENT_MAX and "\n" not in s and VALUE_STATEMENT.match(s):
        return s
    return ""


EDIT_PROMPT = """You are the correction stage of the Sierra Pacific cap-app skill.

Here is the client's current dossier (json):
{dossier}

A broker sent this instruction:
{text}

Return ONLY the edits the instruction asks for — never the whole dossier.

{{"edits": [{{"path": "...", "value": ..., "why": "one short line"}}]}}

Path forms, and nothing else:
  company.<field>
  ops_details.<field>            location.<field>          coverages.<field>
  vehicles[<VIN>].<field>        drivers[<full name>].<field>
  loss_runs[<year>].<field>

Rules:
- One entry per field the broker actually asked to change. If the instruction is
  ambiguous about which vehicle or driver, return an empty edits list and say why.
- Never include a field the instruction does not mention. Do not "tidy up",
  do not recompute totals — totals are derived automatically.
- Use null as the value to clear a field.
- Identify vehicles by their full VIN and drivers by their full name, exactly as
  they appear in the dossier above.

Output ONLY the json object, no prose, no fences.
"""


def apply_edit(data: dict, path: str, value) -> str:
    """Apply one `path = value` edit. Returns '' on success or a reason it failed.

    Deliberately narrow: it can only change a field that already exists on a row
    that already exists. Nothing here can add a vehicle, drop a driver, or rewrite
    a section wholesale.
    """
    m = re.match(r"^(\w+)\[([^\]]+)\]\.(\w+)$", path)
    if m:
        listname, ident, field = m.group(1), m.group(2).strip(), m.group(3)
        key = LIST_KEYS.get(listname)
        rows = data.get(listname)
        if not key or not isinstance(rows, list):
            return f"no list called {listname}"
        want = ident.lower()
        if want.startswith("#"):
            # a row with no natural key (a VIN-less truck) is addressed by its
            # position in the list, counting from 0
            try:
                row = rows[int(want[1:])]
            except (ValueError, IndexError):
                row = None
            if isinstance(row, dict):
                row[field] = value
                return ""
            return f"no {listname} row at position {ident}"
        for row in rows:
            if isinstance(row, dict) and str(row.get(key) or "").strip().lower() == want:
                row[field] = value
                return ""
        return f"no {listname} row with {key} = {ident}"
    m = re.match(r"^(\w+)\.(\w+)$", path)
    if m:
        section, field = m.group(1), m.group(2)
        if not isinstance(data.get(section), dict):
            return f"no section called {section}"
        data[section][field] = value
        return ""
    return f"unrecognised path {path}"


def derive_totals(data: dict) -> None:
    """Recompute vehicle_totals from the list. Arithmetic is never a model's job.

    A correction to one truck's stated value once came back with a different truck
    re-valued at 11223 — the last five characters of its own VIN, 1FDUF4GT6NEC11223
    — and the total silently recomputed to match, so every cross-check passed while
    a $58,000 unit sat on the file insured for $11,223. Totals are now derived from
    the rows, which makes that class of drift impossible rather than detectable.
    """
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list):
        return
    if not vehicles:
        # A fleet emptied by a removal must not keep the old fleet's numbers:
        # Brookfield's file read "0 vehicles" while vehicle_totals still
        # said one power unit worth $25,000 — the app would have gone out
        # claiming a truck that was no longer on it.
        if isinstance(data.get("vehicle_totals"), dict):
            data["vehicle_totals"] = {"stated_value": None, "power_units": 0,
                                      "trailers": 0}
        return
    totals = data.setdefault("vehicle_totals", {})
    if not isinstance(totals, dict):
        data["vehicle_totals"] = totals = {}
    values = [v.get("stated_value") for v in vehicles
              if isinstance(v, dict) and isinstance(v.get("stated_value"), (int, float))]
    # No row states a value -> the total is unknown, not last month's number. A
    # replaced truck left "$25,000 / 1 power unit" standing over a fleet whose
    # only row had no value at all.
    totals["stated_value"] = int(sum(values)) if values else None
    trailers = sum(1 for v in vehicles if isinstance(v, dict)
                   and "trailer" in str(v.get("body_type") or "").lower())
    totals["power_units"] = len(vehicles) - trailers
    totals["trailers"] = trailers

    # The declared counts follow the schedule. Left behind, Lakeside's file said
    # "1 vehicle" over a three-truck schedule and produced FOUR separate flags
    # about the same stale number: the conflict, two count mismatches, and the
    # power-units arithmetic. The schedule is the fleet; the count restates it.
    co = data.get("company")
    if isinstance(co, dict):
        co["total_vehicles"] = len(vehicles)
        drivers = data.get("drivers")
        if isinstance(drivers, list) and drivers:
            co["total_drivers"] = len(drivers)


def apply_correction(dossier: dict, text: str, files: list[str]) -> tuple[dict, list[str]]:
    """Carry out a broker's instruction as a set of targeted edits."""
    prompt = EDIT_PROMPT.format(
        dossier=json.dumps({k: v for k, v in dossier.items()
                            if not k.startswith("_")}, indent=1),
        text=text or "(no text)")
    if files:
        prompt += "\n\nATTACHED FILES (read each):\n" + "\n".join(files)
    reply = extract_json(claude_run(prompt, extract_budget(len(files))))

    out = json.loads(json.dumps(dossier))
    notes: list[str] = []
    edits = reply.get("edits") or []
    if not edits:
        notes.append("the instruction could not be turned into a specific change — "
                     "nothing was modified; say which field and which vehicle/driver")
    for e in edits:
        if not isinstance(e, dict) or "path" not in e:
            continue
        err = apply_edit(out, str(e["path"]), e.get("value"))
        if err:
            notes.append(f"could not apply “{e['path']}”: {err}")
    derive_totals(out)
    return out, notes


ROWOPS_PROMPT = """You are the correction stage of the Sierra Pacific cap-app skill.

Here is the client's current dossier (json), with the new drop's plain additions
already folded in. Rows that arrived WITH THIS DROP are marked
"_new_in_this_drop": true; every other row was already on file before it:
{dossier}

The broker's drop carried an instruction:
{text}

Everything the drop's attachments contained has ALREADY been read into this json
(the "new material"; list rows are in order, the first row is number 0):
{fresh}

Return ONLY the operations the instruction asks for:

{{"ops": [
 {{"set": {{"path": "vehicles[<VIN>].stated_value", "value": 100000}}, "why": "one short line"}},
 {{"remove": {{"path": "vehicles[<VIN>]"}}, "why": "one short line"}},
 {{"add": {{"section": "vehicles", "from_new": 0}}, "why": "one short line"}}
]}}

Rules:
- "set" changes one existing field on one existing row or section. Path forms,
  and nothing else: company.<field>  ops_details.<field>  location.<field>
  coverages.<field>  vehicles[<VIN>].<field>  drivers[<full name>].<field>
  loss_runs[<year>].<field>. A row with no VIN/name/year is addressed by its
  position instead, counting from 0: vehicles[#1].
- "remove" deletes one whole existing row; same row forms as "set".
- "add" copies row number <from_new> of that section FROM THE NEW MATERIAL json,
  exactly as it is. You never type row data yourself; if the new material does
  not contain the row the broker means, return no add and explain in "why".
- "Replace X" / "replace the other X" means the row ALREADY ON FILE goes and the
  row from this drop stays. So: remove the row WITHOUT "_new_in_this_drop", and
  skip the add — the merge already put the new one in the dossier above. NEVER
  remove a row marked "_new_in_this_drop": true; that is the material the broker
  just sent, and deleting it throws the drop away. If the two rows look alike
  (same year and make), that is usually a misread digit, not the same truck —
  the marker decides, not the resemblance.
- Only what the instruction asks: never tidy up, never recompute totals.
- If the instruction is ambiguous about which row it means, return {{"ops": [],
  "why": "..."}} saying what to ask the broker.

Output ONLY the json object, no prose, no fences.
"""


def _find_row(data: dict, listname: str, ident_text: str):
    """Resolve vehicles[<VIN>] / vehicles[#1] / drivers[<name>] to its row."""
    rows = data.get(listname)
    key = LIST_KEYS.get(listname)
    if not key or not isinstance(rows, list):
        return None
    ident = ident_text.strip()
    if ident.startswith("#"):
        try:
            row = rows[int(ident[1:])]
        except (ValueError, IndexError):
            return None
        return row if isinstance(row, dict) else None
    want = _row_key(listname, ident)
    for row in rows:
        if isinstance(row, dict) and _row_key(listname, row.get(key)) == want:
            return row
    return None


def _new_row_start(listname: str, prior: dict) -> int:
    """Index at which rows appended by this drop begin.

    Position, not resemblance. Identity was the obvious way to tell a new row
    from one on file, and it is the one thing that cannot be trusted here: the
    replacement truck had no VIN and its handwritten 2022 was read as 2012, the
    exact year and make of the truck already on file, so every content-based test
    called it "already known". merge_dossier only ever appends, in order, so
    anything past the count that was on file arrived with this drop — true even
    when two rows are indistinguishable.
    """
    return len([r for r in (prior.get(listname) or []) if isinstance(r, dict)])


def _mark_new_rows(merged: dict, prior: dict) -> dict:
    """A copy of the merged dossier whose list rows say whether they arrived with
    this drop. Without it "Replace the other truck" is a coin flip: the ops model
    saw a 2012 International on file and a 2012 International from the drop and
    removed the NEW one, throwing away exactly what the broker had just sent."""
    out = json.loads(json.dumps({k: v for k, v in merged.items()
                                 if not k.startswith("_")}))
    for key in LIST_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        for r in rows[_new_row_start(key, prior):]:
            if isinstance(r, dict):
                r["_new_in_this_drop"] = True
    return out


def apply_instruction(dossier: dict, text: str, fresh: dict,
                      prior: dict | None = None) -> tuple[dict, list[str], str]:
    """Carry out an instruction that came WITH attachments, as row-level ops.

    The old route re-read every attachment against the dossier and asked for the
    full updated file back — a second multi-minute image pass — and the per-image
    answers were then folded with the blank-filling merge, which cannot express a
    removal: on Brookfield's "Replace other truck" email the old truck
    survived every merge and the run reported "no changes". Here the attachments
    are read once, dossier-free; their content is handed over as json; and the
    model only NAMES operations. A row it adds is copied from that json by code,
    so it cannot type a VIN of its own.
    """
    clean_fresh = {k: v for k, v in (fresh or {}).items()
                   if not k.startswith("_") and k != "red_flags"}
    prompt = ROWOPS_PROMPT.format(
        dossier=json.dumps(_mark_new_rows(dossier, prior or {}), indent=1),
        text=text or "(no text)",
        fresh=json.dumps(clean_fresh, indent=1))
    # Its own budget, and its own failure mode. This call reads two full dossiers
    # of json, which is slower than the 120s default meant for a one-line
    # correction — it ran out at exactly 120s and the TimeoutExpired threw away
    # the 97 seconds of image reading that had already succeeded. A drop must
    # never be lost by the smallest step in it: if the ops call fails, the
    # deterministic merge still stands and the broker is told, loudly, that the
    # instruction itself was not carried out.
    try:
        reply = extract_json(claude_run(prompt, 300))
    except Exception as exc:  # noqa: BLE001
        return dossier, [
            f"the new material was merged, but the instruction “{text.strip()[:60]}” "
            f"could NOT be carried out ({type(exc).__name__}) — nothing was "
            f"removed or replaced; apply it by hand or send it again on its own"
        ], ""

    stage("instruction ops")
    out = json.loads(json.dumps(dossier))
    # Which row objects arrived with this drop, captured BEFORE any op runs.
    # Object identity, so a later `set` that edits a row or a removal that
    # shifts the list cannot make the guard point at the wrong truck.
    new_rows = {id(r) for lk in LIST_KEYS
                for r in (out.get(lk) or [])[_new_row_start(lk, prior or {}):]
                if isinstance(r, dict)}
    notes: list[str] = []
    done: list[str] = []
    ops = reply.get("ops") or []
    if not ops:
        why = str(reply.get("why") or "").strip()
        notes.append("the instruction could not be turned into a specific change "
                     "— nothing beyond the plain merge was applied"
                     + (f": {why}" if why else "; say which vehicle/driver"))
    for e in ops:
        if not isinstance(e, dict):
            continue
        why = str(e.get("why") or "").strip()
        if "set" in e:
            spec = e.get("set") or {}
            err = apply_edit(out, str(spec.get("path", "")), spec.get("value"))
            if err:
                notes.append(f"could not apply “{spec.get('path')}”: {err}")
            else:
                done.append(f"set {spec.get('path')} = {spec.get('value')}"
                            + (f" ({why})" if why else ""))
        elif "remove" in e:
            path = str((e.get("remove") or {}).get("path", ""))
            m = re.match(r"^(\w+)\[([^\]]+)\]$", path)
            row = _find_row(out, m.group(1), m.group(2)) if m else None
            if row is not None and id(row) in new_rows:
                # The material the broker just sent is never what "replace" is
                # asking to delete. Refusing here is the hard stop behind the
                # prompt's rule: the model removed the new truck once and the
                # replacement went in the bin while the old truck stayed.
                notes.append(f"refused to remove “{path}”: that row came from "
                             f"THIS drop, so it is what the instruction is "
                             f"adding, not what it is replacing — say which "
                             f"unit to drop if this is wrong")
            elif row is not None:
                out[m.group(1)].remove(row)
                done.append(f"removed {path}" + (f" ({why})" if why else ""))
            else:
                notes.append(f"could not remove “{path}”: no such row")
        elif "add" in e:
            spec = e.get("add") or {}
            section = str(spec.get("section", ""))
            if section not in LIST_KEYS:
                notes.append(f"rows can only be added to "
                             f"{', '.join(LIST_KEYS)} — not “{section}”")
                continue
            src_rows = clean_fresh.get(section) or []
            try:
                row = src_rows[int(spec.get("from_new", -1))]
            except (TypeError, ValueError, IndexError):
                row = None
            if not isinstance(row, dict):
                notes.append(f"could not add to {section}: the new material has "
                             f"no row {spec.get('from_new')!r}")
                continue
            # The same keyed fold as everywhere else, so adding a row the merge
            # already put there folds instead of duplicating. But a row the
            # broker EXPLICITLY asked for is data, not noise: if the fold drops
            # it for having no VIN, it is appended anyway. This reported
            # "added a vehicles row" while the fleet ended up empty.
            before = len(out.get(section) or [])
            out = merge_dossier(out, {section: [row]})
            after = len(out.get(section) or [])
            landed = after > before or any(
                all(str(r.get(k)) == str(v) for k, v in row.items()
                    if not _blank(v))
                for r in (out.get(section) or []) if isinstance(r, dict))
            if not landed:
                out.setdefault(section, []).append(json.loads(json.dumps(row)))
                notes.append(f"the {section} row the instruction added has no "
                             f"{LIST_KEYS[section]} — it is on the file, but it "
                             f"cannot be verified against DMV or a carrier until "
                             f"a human supplies it")
            done.append(f"added a {section} row from the drop"
                        + (f" ({why})" if why else ""))
    derive_totals(out)
    return out, notes, "; ".join(done)[:200]


GENERIC_WORDS = {"towing", "tow", "recovery", "transport", "llc", "inc", "corp",
                 "company", "auto", "truck", "trucking", "service", "services"}


def find_client_in_text(text: str) -> str:
    """Which existing client does this text talk about? '' when unclear.

    Deterministic and cheap: every client's distinctive name words are checked
    against the message. "el valor del camión de lakeside está mal" resolves by
    the word 'lakeside' without a model call. Generic industry words never count —
    half the book of business contains "towing".

    The SP code is checked first and wins outright. JC's rule, on the 7.29 call:
    "we always want to use the SP name because it's our unique identifier" — his
    team writes FALCO1, not Falcon Ridge, precisely because the names arrive
    misspelled and half his book reads alike (North Valley, Northside, North 12).
    A code is matched as a whole word, so "lakeside" is never read as LAKES.
    """
    low = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())
    words = set(low.split())
    by_name: list[str] = []
    by_code: dict[str, str] = {}
    for d in sorted(CLIENTS.iterdir()) if CLIENTS.is_dir() else []:
        if not (d / "state.json").exists():
            continue
        try:
            state = json.loads((d / "state.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        code = str(state.get("sp_code") or "").strip().lower()
        if code:
            by_code.setdefault(code, d.name)
        c = state.get("company", {}) or {}
        names = f"{c.get('first_named_insured', '')} {c.get('dba', '')}".lower()
        distinctive = {w for w in re.sub(r"[^a-z0-9 ]", " ", names).split()
                       if len(w) >= 4 and w not in GENERIC_WORDS}
        if distinctive & words:
            by_name.append(d.name)

    hits = {by_code[w] for w in words if w in by_code}
    if hits:
        # one code settles it even when the names in the same message do not;
        # two codes never do
        return hits.pop() if len(hits) == 1 else ""
    return by_name[0] if len(by_name) == 1 else ""


def build_vendor_apps(data: dict, slug: str, caption: str = "") -> dict:
    """Phase 1 is two documents, not one. The CAP app is always made; the RTS
    Excel is made whenever JC's routing rule says the risk qualifies, without
    anyone having to ask for it — "start with the SP cap app and the RTS app
    every time".

    `caption` is the message the drop arrived with, and it is here because the
    rule and an instruction are not the same kind of statement. The rule answers
    what a risk earns on its own. When a broker names the document, that outranks
    it: JC posted "Prep new CAP app + RTS Prog app" on a risk the rule correctly
    excludes (Arizona, general freight), and with no access to those words the
    engine reported a routing note as if nobody had asked. Overruling a licensed
    broker in silence is the automation deciding, not flagging.

    Returns {'rts': {...}} describing what happened, including the reason when
    nothing was made, because "no RTS", "we couldn't tell" and "the rule said no
    but you asked" are three different pieces of news for the broker.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reports"))
    import routing

    decision = routing.rts_applies(data)
    applies, reason, unknown = decision.applies, decision.reason, decision.unknown
    overridden, rule_said = False, ""
    if not applies and routing.asked_for_rts(caption):
        # The rule's own words are kept as their own field, not spliced into the
        # reason and pulled back out downstream. "Built because you asked" teaches
        # the reader nothing; "the rule would have said no, and here is what it
        # said" is what lets a broker catch their own slip.
        applies, unknown, overridden = True, False, True
        rule_said = reason.rstrip(". ")
        reason = (f"asked for by name in the message, so it was built. The "
                  f"routing rule would have said no: {rule_said}.")
    out = {"applies": applies, "reason": reason, "unknown": unknown,
           "overridden": overridden, "rule_said": rule_said,
           "file": "", "cells": 0}
    if not applies:
        return {"rts": out}
    # Ordering guard. This used to run before the SP code was written to the
    # file, and rts_fill's old fallback shipped "CLIENT CAP RTS supp app" to a
    # client folder. The code is anchored earlier now; if that ever regresses,
    # stop here rather than deliver a misnamed document.
    if not data.get("sp_code"):
        out["error"] = ("no SP code anchored on the file yet — the RTS was "
                        "not built rather than named wrong")
        return {"rts": out}
    try:
        import rts_fill
        r = rts_fill.fill(slug)
        if not r.get("ok", True):
            out["error"] = r.get("error", "the RTS fill failed")
            return {"rts": out}
        out["file"] = r.get("file", "")
        out["cells"] = r.get("cells", 0)
        out["unknown_blanks"] = r.get("unknown_blanks") or []
        out["dropped"] = r.get("dropped") or []
    except Exception as exc:  # noqa: BLE001 - the CAP app must still be delivered
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return {"rts": out}


def carry_history(on_file: dict | None, fresh: dict) -> dict:
    """Put the file's own record back on a dossier that came from a model.

    Everything under `_` — findings, reading notes, the hash-chained changelog —
    is the ENGINE's record of what happened, not client data. It is stripped
    out of every prompt on the way in, so a model can only ever hand back the
    client half. Writing that half straight to state.json deletes the rest.

    That is not hypothetical: a reply of "el FEIN es 88-3410999" in a known
    thread took this path and wiped twenty findings off Falcon Ridge's file and
    two off Shoreline's, after both had been announced in Slack. `_changelog`
    escaped only because it happened to be re-attached by hand further down —
    a hand-maintained list that `_red_flags` was never added to. This carries
    ALL of them, so the next key someone invents is covered by construction.

    A key the fresh pass genuinely produced wins: this restores what was
    dropped, it never overwrites live work.
    """
    for key, value in (on_file or {}).items():
        if key.startswith("_") and key not in fresh:
            fresh[key] = value
    return fresh


def archive_sources(files: list[str], source_dir: Path,
                    stamp_file: str) -> tuple[list[str], list, list[str]]:
    """Copy this drop's materials into _source. Returns (archived, to_upload, reused).

    Same document re-sent = same archive entry: the NICO scan arrived three
    times in one test and _source kept all three under different timestamps,
    locally AND in Drive. Identity is (original name, byte size) — a re-send is
    skipped, a genuinely revised file (same name, different bytes) is kept.

    But a skipped re-send still has to be CREDITED. FALCO1's email screenshot
    was dropped a second time, correctly not copied, and the audit entry then
    named no source at all while three trucks changed value. `reused` is what
    keeps provenance on the record without duplicating bytes.
    """
    existing = {}
    for q in source_dir.iterdir():
        if q.is_file():
            clean = re.sub(r"^\d{8}_\d{4}_", "", q.name)
            existing[(clean, q.stat().st_size)] = q

    archived: list[str] = []
    to_upload: list = []
    reused: list[str] = []
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        clean = p.name.split("_", 1)[-1]
        if (clean, p.stat().st_size) in existing:
            reused.append(f"{clean} (already on file)")
            continue
        dest = source_dir / f"{stamp_file}_{clean}"
        shutil.copyfile(p, dest)
        archived.append(dest.name)
        to_upload.append(p)
    return archived, to_upload, reused


def source_line(archived: list[str], reused: list[str]) -> str:
    """The audit entry's `source` field: everything this change came off."""
    return ", ".join(list(archived) + list(reused))


LIST_KEYS = {"vehicles": "vin", "drivers": "name", "loss_runs": "year"}


def _row_key(listname: str, value) -> str:
    """Identity of a list row, normalised. Names match across word order and
    the comma form: loss-run claim grids print "Lakeside, Salomon" while the
    application says "Salomon Lakeside" — one person, and treating those as two
    grew a phantom second driver on a one-driver policy."""
    v = str(value or "").strip().lower()
    if listname == "drivers":
        if "," in v:
            last, _, first = v.partition(",")
            v = f"{first.strip()} {last.strip()}"
        return " ".join(sorted(v.split()))
    return v


def _dedupe_claims(entry: dict) -> None:
    claims = entry.get("claims")
    if not isinstance(claims, list):
        return
    seen, out = set(), []
    for c in claims:
        if not isinstance(c, dict):
            continue
        key = (c.get("date_of_loss"), c.get("type"), c.get("total_incurred"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    entry["claims"] = out


def _blank(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _vehicle_print(row: dict) -> str:
    """Fallback identity for a truck that arrived without its VIN.

    A handwritten KBK schedule carries year/make/body/value but rarely a VIN —
    the VIN lives on the registration card photographed separately. Under the
    no-key rule both rows were dropped as noise, which silently lost the
    replacement truck on Brookfield's email. Year + the first three letters
    of the make ("INTL" and "INTERNATIONAL" must agree) lets the schedule row
    and the registration row land as ONE truck, whichever is read first."""
    year = str(row.get("year") or "").strip()
    # the schema field is "maker"; extractions have produced both spellings,
    # and reading only one of them turned the fingerprint into a no-op
    make = re.sub(r"[^a-z]", "",
                  str(row.get("maker") or row.get("make") or "").lower())[:3]
    return f"{year}|{make}" if year and make else ""


def _rows_disagree(a: dict, b: dict) -> bool:
    """True when the rows state DIFFERENT values for a field they both answer —
    the guard that keeps two same-year-same-make trucks from collapsing into
    one row just because neither photo showed a VIN. Year and make are skipped:
    this is only ever called on a fingerprint match, which already judged them
    equal — comparing the raw strings again would split "INTL" from
    "INTERNATIONAL", the exact variance the fingerprint absorbs."""
    for k, v in b.items():
        if k in ("year", "make", "maker"):
            continue
        w = a.get(k)
        if _blank(v) or _blank(w):
            continue
        if str(v).strip().lower() != str(w).strip().lower():
            return True
    return False


def _one_letter_apart(a: str, b: str) -> bool:
    """True when the two normalised names differ by exactly one edit —
    a substituted, missing, or extra character. Handwriting read as "Pavel
    Stupan" must fold into the "Pavel Stupak" already on file instead of
    becoming a second driver; anything further apart is treated as a genuinely
    different person, because family fleets really do employ near-namesakes."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = diffs = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        diffs += 1
        if diffs > 1:
            return False
        if la == lb:
            i += 1
        j += 1
    return True


def _dedupe_rows(rows: list) -> list:
    """Collapse rows that say the same thing, keeping the fuller one.

    The last resort behind every keying rule in this file. A drop of Capitol
    Valley's application ended with two rows both reading "International /
    flatbed / everything else blank" — one from the extraction and one from the
    instruction's add — and neither had a VIN or a year, so no key and no
    fingerprint could tell them apart. Two rows carrying no contradicting fact
    are one unit as far as anyone can prove; if the client really runs two
    identical trucks, the count check ("declares 1, 2 listed") is what asks.
    """
    out: list = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        merged_into = None
        for kept in out:
            if isinstance(kept, dict) and not _rows_contradict(kept, row):
                merged_into = kept
                break
        if merged_into is None:
            out.append(row)
        else:
            for k, v in row.items():
                if not _blank(v) and _blank(merged_into.get(k)):
                    merged_into[k] = v
    return out


def _rows_contradict(a: dict, b: dict) -> bool:
    """True when both rows answer the same field with different values."""
    for k, v in b.items():
        if k.startswith("_") or _blank(v) or _blank(a.get(k)):
            continue
        if str(v).strip().lower() != str(a[k]).strip().lower():
            return True
    return False


def dedupe_lists(data: dict) -> list[str]:
    """Dedupe every keyed list in the dossier. Returns notes for what collapsed."""
    notes: list[str] = []
    for key in LIST_KEYS:
        rows = data.get(key)
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        deduped = _dedupe_rows(rows)
        if len(deduped) < len(rows):
            data[key] = deduped
            notes.append(f"{len(rows) - len(deduped)} duplicate {key} row(s) "
                         f"carrying no distinguishing detail were folded into "
                         f"one — confirm the unit count")
    return notes


# Sentences that describe how the engine did its work, not something a human has
# to decide. They belong in state.json and the change log, never in the message:
# a broker handed fourteen numbered points reads none of them, which is the same
# as having said nothing at all.
HOUSEKEEPING = (
    "square to the form",          # condense_notes phrases it "aren't square"
    "yes/no boxes",
    "identifiers came from per-image reads",
    "unpacked", "attachment(s) from the email",
    "was read on its own and that reading is the one on file",
    "so it was applied as an instruction",
    "crop re-read unavailable",
    "recovered by a focused re-read",
    "did not come through the general reading",
)

# The opposite: a human is being asked for something, or the engine refused to
# act. These lead, in this order, whatever else is on the list.
LEADS = (
    "could NOT be carried out",
    "could not be turned into a specific change",
    "refused to remove",
    "no legible VIN",
    "fails its check digit",
    "one letter away",
    "duplicate",
    "cannot be verified",
)


def headline(op: str, changes: list[dict], data: dict) -> str:
    """One line: what this drop actually did to the file.

    The first sentence has to be the answer. "Replaced the truck: 2012
    International off, 2022 flatbed on" is the whole message for most drops; the
    flags below it are the exceptions, not the content.
    """
    gone = [c["from"] for c in changes if c.get("removed")]
    came = [c["to"] for c in changes if c.get("added")]
    if gone and came:
        return f"Swapped: *{', '.join(gone)}* off, *{', '.join(came)}* on"
    if came:
        return f"Added: *{', '.join(came)}*"
    if gone:
        return f"Removed: *{', '.join(gone)}*"
    n_fields = len(changes)
    if op == "create":
        return (f"New file · {len(data.get('vehicles') or [])} vehicle(s), "
                f"{len(data.get('drivers') or [])} driver(s)")
    if n_fields:
        first = humanize(changes[0]["field"])
        return (f"Updated *{first}*" if n_fields == 1
                else f"Updated *{first}* and {n_fields - 1} other field(s)")
    return "Nothing changed on the file"


def triage_flags(id_notes: list[str], model_flags: list[str],
                 auto_flags: list[str]) -> tuple[list[str], int]:
    """Split everything into (what to say, how much was only filed).

    Returns the short list for the broker and the count of housekeeping lines
    left in the record. Nothing is discarded — state.json keeps every note and
    the change log keeps every change; this decides what earns a place in a
    message somebody has three seconds for.
    """
    asks, filed = [], 0
    for n in id_notes:
        if any(h in n for h in HOUSEKEEPING):
            filed += 1
        else:
            asks.append(n)
    asks.sort(key=lambda n: next((i for i, w in enumerate(LEADS) if w in n),
                                len(LEADS)))
    rest = [f for f in list(model_flags) + list(auto_flags) if f not in asks]
    # "this handwriting is illegible" is already said once, for the whole batch,
    # by the grouped reading note. Repeating it per field pushed the missing VIN
    # and the 120% client split out of the four lines anyone actually reads.
    vague = ("illegible", "unclear", "not reliably legible", "handwriting",
             "not clearly", "checkbox", "left blank", "yes/no")
    rest.sort(key=lambda f: 1 if any(v in f.lower() for v in vague) else 0)
    return asks + rest, filed


def condense_notes(notes: list[str]) -> list[str]:
    """Group the per-file notes for the message a human reads.

    Six photographs produce twelve identical sentences — one per file for the
    crop pass and one per file for the yes/no boxes — and the broker's reply
    became a wall of text nobody reads, which is the same as saying nothing. The
    full per-file detail stays in state.json for the audit; this is the summary.
    """
    groups = {
        "crops": ("not square to the form", "photo(s) aren't square to the form — "
                  "identifiers were NOT re-read from magnified crops; treat every "
                  "number as unverified"),
        "bools": ("yes/no boxes not measured", "photo(s): the yes/no boxes could "
                  "not be measured; treat blank answers as unconfirmed, not as no"),
    }
    counts = {k: 0 for k in groups}
    out: list[str] = []
    for n in notes:
        # the yes/no line also contains "not square to the form", so it is
        # tested first — order matters here
        if groups["bools"][0] in n:
            counts["bools"] += 1
        elif groups["crops"][0] in n:
            counts["crops"] += 1
        else:
            out.append(n)
    for k, (_, phrase) in groups.items():
        if counts[k]:
            out.insert(0, f"{counts[k]} {phrase}")
    return out


# Fields where "the drop says one thing, the file says another" is a fact an
# underwriter needs, not noise. Free-text fields are left out on purpose: "CC"
# versus "car carrier" is the same truck described twice, and flagging that class
# of difference is how a message grows to fifteen points nobody reads.
# Attachments whose content is characters, not pixels: one reading, no misreads.
TYPED_SUFFIXES = {".txt", ".md", ".csv", ".json", ".eml", ".msg", ".rtf"}

# Sections computed from the rows, never compared: the drop's own partial total
# (58,000 + 46,000) versus the file's older one (55,000) is not a disagreement
# about anything — the real total is derived after the merge, and it is 159,000.
DERIVED_SECTIONS = {"vehicle_totals"}

CONFLICT_FIELDS = {
    "stated_value", "onhook", "gvw", "gross_revenue", "fein", "usdot",
    "usdot_number", "state_filing_number", "mc_number",
    "current_auto_carrier", "current_auto_expires",
    "birthday", "license_number", "license", "years_experience",
    "policy_effective_date", "radius",
}


def _num(v):
    """The number inside `$58,000` / `25,500` / `150k`, or None."""
    s = str(v or "").strip().lower().replace(",", "").replace("$", "")
    mult = 1000 if s.endswith("k") else 1
    s = s[:-1] if s.endswith("k") else s
    try:
        return float(s) * mult
    except ValueError:
        return None


def _conflicts(old_v, new_v, field: str) -> bool:
    """Do these two answers to the same field genuinely disagree?"""
    if field not in CONFLICT_FIELDS or _blank(old_v) or _blank(new_v):
        return False
    na, nb = _num(old_v), _num(new_v)
    if na is not None and nb is not None:
        return abs(na - nb) > 0.01               # 25500 and "25,500" agree
    if field in ("birthday", "current_auto_expires", "policy_effective_date"):
        da, db = m8_date(old_v), m8_date(new_v)
        if da and db:
            return da != db
    return _norm_id(old_v) != _norm_id(new_v)


def _extend_notes(data: dict, new_notes) -> None:
    """Notes persist in the state (QP findings are built from them), so the
    same photo re-sent must not append its 'not square to the form' line twice."""
    have = data.setdefault("_identifier_notes", [])
    for n in new_notes or []:
        if n not in have:
            have.append(n)


def merge_dossier(old: dict, new: dict, bridge_keyless: bool = False) -> dict:
    """Fold a fresh extraction into an existing dossier, filling blanks only.

    An arriving drop adds to what is on file; it does not get to overwrite it. A
    broker who wants a value changed says so, and that instruction goes through
    the CORRECT flow where a model reads the request. Here, silence from the new
    material must never erase an answer that was already collected — the loss run
    that arrives with only a carrier name should not blank out the phone number.

    Lists merge by their natural key so the same truck arriving twice stays one
    truck: vehicles by VIN, drivers by name, loss runs by year.
    """
    out = json.loads(json.dumps(old))          # deep copy, no shared references
    for key, val in (new or {}).items():
        if key.startswith("_") or key == "red_flags":
            continue
        if key in LIST_KEYS and isinstance(val, list):
            ident = LIST_KEYS[key]
            rows = out.get(key)
            if not isinstance(rows, list):
                out[key] = val
                continue
            by_id = {_row_key(key, r.get(ident)): r
                     for r in rows if isinstance(r, dict) and r.get(ident)}
            prints: dict = {}
            if key == "vehicles":
                for r in rows:
                    if isinstance(r, dict) and _vehicle_print(r):
                        prints.setdefault(_vehicle_print(r), []).append(r)
            for row in val:
                if not isinstance(row, dict):
                    continue
                rid = _row_key(key, row.get(ident))
                fp = _vehicle_print(row) if key == "vehicles" else ""
                if not rid and not fp:
                    # A row without any identity is noise, not data: a loss-run
                    # term with no year cannot be matched on the next pass and
                    # just accumulates as duplicates — stage 2 of the Lakeside
                    # test grew two year-less "Amwins TUMI" rows this way.
                    continue
                target = by_id.get(rid) if rid else None
                if target is None and rid and key == "drivers":
                    # A name one letter away from a driver on file is a misread
                    # of the same handwriting, not a new hire: "Pavel Stupan"
                    # from a 640px photo grew a phantom second driver next to
                    # "Pavel Stupak". Fold, and say so out loud.
                    near = next((k for k in by_id if _one_letter_apart(rid, k)),
                                None)
                    if near:
                        target = by_id[near]
                        _extend_notes(out, [
                            f"driver “{row.get(ident)}” from this drop is one "
                            f"letter away from “{target.get(ident)}” already on "
                            f"file — treated as the same person; verify the "
                            f"spelling against a license or MVR"])
                if target is None and fp:
                    # A truck with no VIN still has an identity: year + make.
                    # Inside ONE drop that is safe and necessary — the schedule
                    # photo and the registration-card photo are the same
                    # physical unit and must land as one row.
                    #
                    # Across drops it is not safe, and this is not theoretical:
                    # the handwritten "2022 International" on Brookfield's
                    # schedule was read as "2012", which is exactly the year and
                    # make of the 2012 International already on file. The bridge
                    # folded the replacement truck into the truck it was meant
                    # to replace, the instruction removed that one row, and the
                    # fleet went to zero vehicles. A visible duplicate that a
                    # human resolves beats a silent collapse of two units, so
                    # here the row is kept apart and the collision is announced.
                    # Every row already carrying this fingerprint is a candidate,
                    # keyless ones first: on a re-send the row to fold into is
                    # the VIN-less one appended last time, not the VIN'd truck
                    # that shares its year and make. Checking only the first
                    # match duplicated the row on every replay.
                    same = prints.get(fp) or []
                    cands = [c for c in same if not c.get(ident)] + \
                            [c for c in same if c.get(ident)]
                    for cand in cands:
                        agree = not _rows_disagree(cand, row)
                        if agree and (bridge_keyless or not cand.get(ident)):
                            target = cand
                            break
                        if cand.get(ident):
                            _extend_notes(out, [
                                f"this drop shows a {row.get('year')} "
                                f"{row.get('maker') or row.get('make')} with no "
                                f"VIN, and VIN {cand.get(ident)} on file is the "
                                f"same year and make — listed as a SEPARATE "
                                f"unit; confirm whether it is the same truck "
                                f"(a misread year does this) before quoting"])
                if target is None:
                    rows.append(row)                       # a genuinely new row
                    if rid:
                        by_id[rid] = row
                    if fp:
                        prints.setdefault(fp, []).append(row)
                else:
                    for k, v in row.items():               # fill this row's blanks
                        if not _blank(v) and _blank(target.get(k)):
                            target[k] = v
                        elif _conflicts(target.get(k), v, k):
                            _extend_notes(out, [
                                f"{key[:-1]} “{_row_blurb(target)}”: this drop "
                                f"says {k} is {v}, the file says "
                                f"{target.get(k)} — the file was NOT changed; "
                                f"say which is right"])
                    _dedupe_claims(target)
                    if rid:
                        by_id.setdefault(rid, target)
        elif isinstance(val, dict):
            section = out.get(key)
            if not isinstance(section, dict):
                out[key] = val
                continue
            for k, v in val.items():
                if not _blank(v) and _blank(section.get(k)):
                    section[k] = v
                elif key in DERIVED_SECTIONS:
                    continue
                elif _conflicts(section.get(k), v, k):
                    # Kept, not overwritten — a drop adds to what is on file and
                    # only an explicit instruction changes it. But saying nothing
                    # is worse than either: the drop reported $340,000 of revenue
                    # over $90,000 on file and the difference vanished.
                    _extend_notes(out, [
                        f"{key}.{k}: this drop says {v}, the file says "
                        f"{section.get(k)} — the file was NOT changed; say "
                        f"which is right"])
        elif not _blank(val) and _blank(out.get(key)):
            out[key] = val
        elif _conflicts(out.get(key), val, key):
            _extend_notes(out, [
                f"{key}: this drop says {val}, the file says {out.get(key)} "
                f"— the file was NOT changed; say which is right"])
    return out


_VIN_SHAPE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")   # no I/O/Q in a VIN
_PROGRESSIVE_DOC = re.compile(r"\bprogressive\b", re.I)


def progressive_vins(files: list[str]) -> set[str]:
    """VINs printed on Progressive paper in this drop, or empty.

    JC's precedence rule (7.29): "if we get a document for Progressive and
    there's a mismatch on VIN numbers, go with the Progressive one… that's
    more reliable." The rule only fires for VINs that are actually ON the
    Progressive document — a dec page in the same drop as a handwritten
    schedule does not make the schedule gospel. Text-bearing PDFs only;
    anything unreadable contributes nothing rather than crashing the drop.
    """
    import fitz  # local, like every other fitz use in this module

    vins: set[str] = set()
    for f in files or []:
        if not str(f).lower().endswith(".pdf"):
            continue
        try:
            with fitz.open(f) as doc:
                text = "".join(p.get_text() for p in doc)
        except Exception:  # noqa: BLE001 - an unreadable file is not evidence
            continue
        if _PROGRESSIVE_DOC.search(text):
            vins |= {m.group(1) for m in _VIN_SHAPE.finditer(text.upper())}
    return vins


def reconcile_progressive_vins(data: dict, prog_vins: set[str]) -> list[str]:
    """Collapse a same-truck VIN mismatch in Progressive's favour, in place.

    Vehicles are keyed by VIN, so a Progressive document carrying a different
    VIN for the same physical unit lands as a SECOND truck next to the first
    (Lakeside, day one: the app's 2016 Hino and the COI's 2018 Hino). When one
    of the pair carries the VIN printed on Progressive paper and the other
    does not, the pair is one truck and Progressive's VIN is the right one.
    Both VINs on the paper = a genuine two-unit policy; both off it = not
    Progressive's call. Returns the notes describing what moved.
    """
    notes: list[str] = []
    if not prog_vins:
        return notes
    vehicles = [v for v in (data.get("vehicles") or []) if isinstance(v, dict)]
    by_print: dict[str, list[dict]] = {}
    for v in vehicles:
        fp = _vehicle_print(v)
        if fp and v.get("vin"):
            by_print.setdefault(fp, []).append(v)

    for fp, group in by_print.items():
        winners = [v for v in group if str(v.get("vin")).upper() in prog_vins]
        losers = [v for v in group if str(v.get("vin")).upper() not in prog_vins]
        if len(winners) != 1 or not losers:
            continue                    # 0 = not our call; 2+ = two real units
        keeper = winners[0]
        for loser in losers:
            for k, val in loser.items():           # the file's detail survives
                if k != "vin" and not _blank(val) and _blank(keeper.get(k)):
                    keeper[k] = val
            data["vehicles"].remove(loser)
            notes.append(
                f"Progressive wins a VIN mismatch (JC's rule): the "
                f"{loser.get('year')} {loser.get('maker') or loser.get('make')} "
                f"on file carried VIN {loser.get('vin')}, the Progressive "
                f"document prints {keeper.get('vin')} — kept Progressive's, "
                f"folded the rest of the unit's detail together")
    return notes


PARALLEL_EXTRACT_FROM = 3       # attachments; below this, one call is cheaper
MAX_EXTRACT_WORKERS = 3


def parallel_extract(text: str, files: list[str], dossier: dict | None) -> dict:
    """Extract from many attachments at once, then merge the pieces in code.

    A forwarded email with six photographs took 158s as a single call and kept
    overrunning the pipeline. The images are independent — nothing in photo 4
    depends on photo 1 — so they are read concurrently and the partial results
    folded together with the same blank-filling merge used elsewhere. Each call
    also has less to look at, which makes each one both faster and more accurate.
    """
    import concurrent.futures as cf

    groups = [[f] for f in files]
    parts: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=MAX_EXTRACT_WORKERS) as pool:
        futures = {pool.submit(_extract_once, text, g, dossier): g for g in groups}
        for fut in cf.as_completed(futures):
            try:
                parts.append(fut.result())
            except Exception:  # noqa: BLE001 - one blind photo must not sink the drop
                continue
    if not parts:
        raise RuntimeError("every attachment failed to extract")

    # Fold the pieces together. Richest first, so the photo that actually carried
    # the schedule sets the shape and the rest only fill gaps.
    parts.sort(key=lambda d: -len(json.dumps(d)))
    merged = parts[0]
    for extra in parts[1:]:
        # bridge_keyless: these are photographs of ONE drop, so the schedule page
        # and the registration card are the same truck and must land as one row
        merged = merge_dossier(merged, extra, bridge_keyless=True)
    return merged


def _extract_once(text: str, files: list[str], dossier: dict | None) -> dict:
    if dossier is None:
        prompt = CREATE_PROMPT.format(schema=SCHEMA.read_text(encoding="utf-8"),
                                      text=text or "(no text, see image)")
    else:
        clean = {k: v for k, v in dossier.items() if not k.startswith("_")}
        prompt = UPDATE_PROMPT.format(dossier=json.dumps(clean, indent=1),
                                      text=text or "(no text, see image)")
    if files:
        prompt += "\n\nATTACHED FILES (read each):\n" + "\n".join(files)
    return extract_json(claude_run(prompt, extract_budget(len(files))))


SWEEP_PROMPT = """Read the attached file(s) and find the VEHICLE SCHEDULE only.

On a KBK/tow application it is the grid headed "VEHICLE SCHEDULE" with numbered
"Vehicle #" blocks: Year, Make, Model, Body Type, GVW, Class Code, On-Hook Limit,
Deductibles, Stated Amount, Radius, Use of vehicle. It is usually handwritten. A
vehicle registration card counts too: it carries year, make and the VIN.

Return ONLY this json:
{"vehicles": [{"year": 2022, "maker": "...", "model": "...", "body_type": "...",
               "gvw": 26000, "stated_value": 100000, "onhook": "...",
               "vin": "..."}]}

Rules:
- One entry per vehicle you can actually see written in. Skip the blank blocks.
- Omit any field that is not filled in, and use null for a value you cannot read
  with confidence. Do NOT guess a digit and do NOT guess a VIN — a wrong VIN is
  worse than a missing one.
- Report the year exactly as written even if it looks unusual.
- If there is no vehicle schedule and no registration card, return
  {"vehicles": []}.
- Do not crop, zoom or write files.

Output ONLY the json object, no prose, no fences.

ATTACHED FILES (read each):
{files}
"""


def vehicle_sweep(data: dict, files: list[str]) -> tuple[dict, list[str]]:
    """One focused pass for the vehicle schedule when the general read missed it.

    The general extraction reads a whole page at a time and is not reliable on
    this grid: the same six photographs of Brookfield's application produced
    the replacement truck on one run and nothing at all on the next, so "Replace
    the other truck" had nothing to replace it with and the drop did nothing. The
    schedule is the one page a tow-fleet packet cannot be built without — it is
    the units being insured — so when no vehicle comes back it is asked for on
    its own, with the field names spelled out and nothing else competing for
    attention.
    """
    try:
        found = extract_json(claude_run(
            SWEEP_PROMPT.replace("{files}", "\n".join(files)), 180))
    except Exception as exc:  # noqa: BLE001
        return data, [f"no vehicle came back from the general reading, and the "
                      f"focused re-read of the vehicle schedule also failed "
                      f"({type(exc).__name__}) — check the schedule page by hand"]
    rows = _dedupe_rows([r for r in (found.get("vehicles") or [])
                         if isinstance(r, dict)
                         and any(not _blank(v) for v in r.values())])
    if not rows:
        return data, []
    # Assigned, not merged. Putting it through merge_dossier threw the recovered
    # truck away for having no VIN — the sweep reported "1 unit recovered" while
    # the fleet stayed at zero. A row this function went looking for on purpose
    # is never noise, and one focused reading of the grid beats whichever of six
    # parallel readers happened to glance at it.
    had = len(data.get("vehicles") or [])
    out = dict(data)
    out["vehicles"] = rows
    stage(f"vehicle schedule sweep ({len(rows)} row(s))")
    if had:
        return out, [f"the vehicle schedule was read on its own and that reading "
                     f"is the one on file ({len(rows)} unit(s)); the general pass "
                     f"had {had} — check year, value and VIN against the photo"]
    return out, [f"the vehicle schedule did not come through the general "
                 f"reading; {len(rows)} unit(s) were recovered by a focused "
                 f"re-read — check year, value and VIN against the photo"]


def _qp_fast_path(files: list[str]) -> tuple[list[str], dict | None, list[str]]:
    """Pull any Sierra Pacific QP/app PDFs out of `files` by reading them.

    JC, on the 7.22 call: "maybe you just upload the QP directly into the
    Slack channel, use this as the basis for all the information to fill out
    the RTS app." reports/qp_read.py already turns a QP's AcroForm fields
    into a dossier dict for the CLI (rts_fill.py --from-qp); this reuses that
    same reader so the Slack drop stops paying for a full model call to
    re-derive values a form field already states outright.

    Detection is by CONTENT, never by filename — a broker renames attachments
    freely. qp_read.read_qp() raises QPReadError for anything not readable as
    a QP (missing file, wrong kind of PDF, too few AcroForm pages); that
    already-tested detector is reused rather than inventing a second one. Any
    OTHER failure while reading a file that got this far (a genuinely
    unexpected error, not just "this isn't a QP") is caught too, so a bad
    attachment falls back to the model instead of crashing the drop.

    Returns (files that still need a model call, the QP(s)' own dossier
    folded into one dict — None if no QP was found, warnings worth a human
    look). Multiple QPs in one drop (a renewal alongside last year's) fold
    together with the same gap-fill merge used everywhere else here.
    """
    other_files: list[str] = []
    qp_dossier: dict | None = None
    notes: list[str] = []
    for f in files:
        try:
            result = qp_read.read_qp(f)
        except qp_read.QPReadError:
            other_files.append(f)          # not a QP — the model still sees it
            continue
        except Exception as exc:  # noqa: BLE001 - a bad QP must fall back, never crash the drop
            other_files.append(f)
            notes.append(f"{Path(f).name}: could not be read as a Sierra Pacific QP "
                         f"({type(exc).__name__}) — sent to the general reader instead")
            continue
        notes.extend(result["warnings"])
        if qp_dossier is None:
            qp_dossier = result["dossier"]
        else:
            qp_dossier, qp_conflicts = qp_read.merge_into_dossier(qp_dossier, result["dossier"])
            notes.extend(f"QP disagrees with another QP in this same drop — {c}"
                         for c in qp_conflicts)
    return other_files, qp_dossier, notes


def claude_extract(text: str, files: list[str], dossier: dict | None) -> dict:
    # Fast path: a QP already carries this data as machine-readable AcroForm
    # fields, so it is read directly instead of being sent through claude_run
    # at real dollar-and-time cost. This has to run before every branch below
    # since, when nothing else needs a model call, it ends the function
    # before a single call is made.
    other_files, qp_dossier, qp_notes = _qp_fast_path(files)

    if qp_dossier is not None and not other_files and not carries_instruction(text):
        # Nothing else in this drop needs a model call — the QP(s) answer it
        # completely. `dossier` is deep-copied before merging so this can
        # never mutate the caller's own object: qp_read.merge_into_dossier
        # does not copy its base (see qp_read.py), the same reason
        # merge_dossier below deep-copies its own "old" argument.
        base = json.loads(json.dumps(dossier)) if dossier else {}
        merged, conflicts = qp_read.merge_into_dossier(base, qp_dossier)
        all_notes = qp_notes + [f"QP disagrees with the file — {c}" for c in conflicts]
        if all_notes:
            _extend_notes(merged, all_notes)
        return merged

    files = other_files     # the QP(s), if any, are already handled above

    if dossier is None:
        schema = SCHEMA.read_text(encoding="utf-8")
        prompt = CREATE_PROMPT.format(schema=schema, text=text or "(no text, see image)")
    else:
        clean = {k: v for k, v in dossier.items() if not k.startswith("_")}
        prompt = UPDATE_PROMPT.format(dossier=json.dumps(clean, indent=1),
                                      text=text or "(no text, see image)")
    if files:
        prompt += "\n\nATTACHED FILES (read each):\n" + "\n".join(files)
    if len(files) >= PARALLEL_EXTRACT_FROM:
        data = parallel_extract(text, files, dossier)
    else:
        data = extract_json(claude_run(prompt, extract_budget(len(files), files)))

    # The vehicle schedule always gets its own reader when photographs are
    # involved, not only when the general pass returns nothing. Across four runs
    # of the same six photographs the general pass gave the truck once, gave it
    # with the year misread as 2012 once, gave it with no year at all once, and
    # missed it entirely once. The focused pass read "2022" — what the paper says
    # — every time. The schedule is the list of units being insured; it does not
    # get to be the least reliable part of the drop.
    sweep_notes: list[str] = []
    if files and (len(files) >= PARALLEL_EXTRACT_FROM
                  or not (data.get("vehicles") or [])):
        data, sweep_notes = vehicle_sweep(data, files)

    # Identifiers get read again from a magnified crop of their own cell, and that
    # reading wins. Measured on the Borderline fixture: whole-page gave a wrong
    # FEIN on three of three attempts and a consistently wrong mobile number; the
    # crop pass returned all four identifiers correctly. See read_ids.py.
    notes: list[str] = list(sweep_notes)
    unscanned = list(files)
    try:
        import read_ids
        scratch = Path(__file__).resolve().parent / "inbox" / "_crops"
        res = read_ids.read_identifiers(files, claude_bin(), scratch)
        for dpath, value in res["values"].items():
            _set_path(data, dpath, value)
        notes += res["notes"]
        unscanned = [f for f in files if Path(f).name not in set(res["scanned"])]

        # Yes/no rows are measured from the ink, not asked. Across the two
        # fixtures the reader turned the same pair of empty squares into `false`
        # for one client and `null` for the other; counting dark pixels inside
        # each box got all ten rows right. An unticked pair stays null so it lands
        # in "questions the client left blank" instead of silently answering no —
        # telematics and dash cameras are premium credits.
        bools = read_ids.read_bool_rows(files)
        for dpath, value in bools["values"].items():
            _set_path(data, dpath, value)
        notes += bools["notes"]
    except Exception as exc:  # noqa: BLE001
        notes.append(f"crop re-read unavailable ({type(exc).__name__}: {exc}); "
                     f"falling back to a second whole-page reading")

    # Anything the crop pass could not handle — a skewed photo, a PDF, an email
    # screenshot — still gets the cheaper safety net: read twice, blank on
    # disagreement. It caught the FEIN even when it could not catch the phone.
    # EXCEPT a parallel photo batch: each image there was already read alone in
    # its own focused call, and a second full-batch read costs another ~150s —
    # it pushed the six-photo email into the pipeline ceiling. Note it instead.
    if unscanned and len(files) >= PARALLEL_EXTRACT_FROM:
        notes.append("identifiers came from per-image reads (photo batch); the "
                     "second verification pass was skipped for time — double-check "
                     "FEIN/VIN/phones against the images before submission")
    elif unscanned:
        notes += verify_identifiers(data, text, unscanned)

    # A truck that arrived without a readable VIN is real data — the schedule
    # photo carries year/make/value while the VIN sits on a registration card —
    # but nothing downstream (DMV, NHTSA, the carrier) can verify it until a
    # human supplies the VIN. Brookfield's registration photo is 148 px
    # wide; its VIN is genuinely illegible and must be asked for, not guessed.
    for v in (data.get("vehicles") or []):
        if not isinstance(v, dict):
            continue
        vin = str(v.get("vin") or "").strip()
        if not vin and _vehicle_print(v):
            notes.append(f"vehicle {v.get('year')} "
                         f"{v.get('maker') or v.get('make')} arrived with "
                         f"no legible VIN — request the VIN or a clearer "
                         f"registration photo before this unit is quoted")
        elif len(vin) == 17:
            try:
                sys.path.insert(0, str(ROOT / "reports"))
                from vin_checkdigit import is_valid
                if not is_valid(vin):
                    notes.append(f"VIN {vin} fails its check digit — likely "
                                 f"misread from the photo; verify against the "
                                 f"registration before it goes on a quote")
            except Exception:  # noqa: BLE001 - validation is best-effort
                pass
    if notes:
        _extend_notes(data, notes)

    if qp_dossier is not None:
        # A QP arrived alongside other material or a correction instruction.
        # The fresh extraction wins — any instruction is applied on top of it
        # afterward, in _main_inner, so this is what lets that instruction
        # outrank the QP snapshot — and the QP only fills what neither the
        # on-file dossier nor this drop's own material already answered.
        data, conflicts = qp_read.merge_into_dossier(data, qp_dossier)
        combined = qp_notes + [f"QP disagrees with the file — {c}" for c in conflicts]
        if combined:
            _extend_notes(data, combined)

    return data


def _set_path(data: dict, dotted: str, value: str) -> None:
    """Write `company.fein` or `vehicles[2].vin` into the extracted dict.

    A crop reading only ever lands on a row that already exists: the whole-page
    pass decides how many vehicles there are, this pass only corrects their
    identifiers. Inventing a row here would let a stray mark in an unused
    schedule line become a vehicle on the policy.
    """
    m = re.match(r"^(\w+)\[(\d+)\]\.(\w+)$", dotted)
    if m:
        listname, idx, key = m.group(1), int(m.group(2)) - 1, m.group(3)
        rows = data.get(listname)
        if isinstance(rows, list) and 0 <= idx < len(rows) and isinstance(rows[idx], dict):
            rows[idx][key] = value
        return
    if "." in dotted:
        section, key = dotted.split(".", 1)
        if isinstance(data.get(section), dict):
            data[section][key] = value
        return
    data[dotted] = value


# ------------------------------------------------------------------ diff / log

def _flat_label(v, i: int) -> str:
    """How a list row is named in a change path."""
    if isinstance(v, dict):
        lab = v.get("vin") or v.get("name") or v.get("year")
        if lab:
            return str(lab)
    return str(i)


def _row_blurb(row: dict) -> str:
    """A truck or a person in a few words, for a line a broker reads."""
    if not isinstance(row, dict):
        return str(row)
    if row.get("name"):
        return str(row["name"])
    bits = [str(row.get(k)) for k in ("year", "maker", "body_type")
            if not _blank(row.get(k))]
    vin = str(row.get("vin") or "")
    if vin:
        # the tail is what a human reads off a registration card; the full
        # seventeen characters in a one-line headline just crowd it out
        bits.append(f"(…{vin[-6:]})")
    return " ".join(bits) or "(no identifying detail)"


def flatten(obj, prefix="") -> dict:
    """Flatten nested dict/list into dotted paths -> scalar values."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{_flat_label(v, i)}]"))
    else:
        out[prefix] = obj
    return out


def diff_dossier(old: dict, new: dict) -> list[dict]:
    """Field-level changes, except that a whole row coming or going is ONE change.

    Swapping one truck for another used to read as fourteen lines — seven fields
    of the old truck going to None and seven of the new arriving — and the broker
    was handed that instead of "the 2012 International came off, the 2022 flatbed
    went on". A removal is one fact, so it is one line.
    """
    changes: list[dict] = []
    skip: set[str] = set()
    for key in LIST_KEYS:
        rows_o = {_flat_label(r, i): r for i, r in enumerate(old.get(key) or [])
                  if isinstance(r, dict)}
        rows_n = {_flat_label(r, i): r for i, r in enumerate(new.get(key) or [])
                  if isinstance(r, dict)}
        for lab in rows_o.keys() - rows_n.keys():
            changes.append({"field": f"{key}[{lab}]", "removed": True,
                            "from": _row_blurb(rows_o[lab]), "to": None})
            skip.add(f"{key}[{lab}]")
        for lab in rows_n.keys() - rows_o.keys():
            changes.append({"field": f"{key}[{lab}]", "added": True,
                            "from": None, "to": _row_blurb(rows_n[lab])})
            skip.add(f"{key}[{lab}]")
    fo, fn = flatten(old), flatten(new)
    for k in sorted(set(fo) | set(fn)):
        if any(k.startswith(s + ".") for s in skip):
            continue
        a, b = fo.get(k), fn.get(k)
        if str(a) != str(b):
            changes.append({"field": k, "from": a, "to": b})
    return changes


def humanize(path: str) -> str:
    """Field path -> plain label, for messages a broker reads."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from audit_pdf import humanize_field
        return humanize_field(path)
    except Exception:  # noqa: BLE001
        return path.replace("_", " ").replace(".", " — ")


def verify_chain(entries: list[dict]) -> tuple[bool, str]:
    """Recompute the hash chain. Returns (intact, message)."""
    prev = GENESIS
    for i, e in enumerate(entries, 1):
        if e.get("prev") != prev:
            return False, f"broken link at entry {i} ({e.get('ts')}): prev hash mismatch"
        if entry_fingerprint(e, prev) != e.get("hash"):
            return False, f"altered content at entry {i} ({e.get('ts')}): fingerprint mismatch"
        prev = e["hash"]
    return True, f"intact — {len(entries)} entries, chain tip {prev}"


def render_changelog(entries: list[dict], client: str, sp: str) -> str:
    intact, msg = verify_chain(entries)
    seal = ":white_check_mark: VERIFIED" if intact else ":rotating_light: TAMPERED"
    lines = [
        f"# Change log — {sp} · {client}",
        "",
        "> **Tamper-evident audit log.** Every entry is hash-chained to the one",
        "> before it. Editing or deleting any entry breaks the chain and is",
        "> detectable. The authoritative copy lives on the Sierra engine, not in",
        "> this folder — this file is a readable mirror.",
        "",
        f"**Integrity check:** {seal} — {msg}",
        "",
        "---",
        "",
    ]
    for e in reversed(entries):  # newest first
        lines.append(f"## {e['ts']} — {e['op'].upper()} by {e['actor']}")
        lines.append(f"{e['summary']}")
        if e.get("source"):
            lines.append(f"- Source: {e['source']}")
        for c in e.get("changes", []):
            frm = "(blank)" if c["from"] in (None, "") else c["from"]
            to = "(blank)" if c["to"] in (None, "") else c["to"]
            lines.append(f"- `{c['field']}`: {frm} → **{to}**")
        lines.append(f"- _entry hash `{e.get('hash', '?')}` ← prev `{e.get('prev', '?')}`_")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ main

def failure_message(detail: str) -> str:
    """Turn a failure into the one sentence a broker should read.

    The wording has to match the cause. "Send fewer/lighter files" was returned
    for an upstream 529 on a drop with NO files, which sends a broker off
    splitting a submission to fix a problem that clears on its own.
    """
    low = (detail or "").lower()
    if "529" in low or "overload" in low or "503" in low:
        return ("Anthropic's API is overloaded right now — nothing wrong with what "
                "you sent. Try the same message again in a minute.")
    if "429" in low or "rate limit" in low or "too many requests" in low:
        return ("hit a rate limit on the model, not a problem with your message — "
                "try again in a moment.")
    if ("api key" in low or "not logged in" in low or "unauthor" in low
            or "401" in low or "credential" in low):
        return ("the engine's Claude login needs attention — it is an auth problem "
                "on our side, not your message. Flagged to Rafael.")
    if "timed out" in low or "timeout" in low:
        return ("one of the reading steps ran past its time budget and was stopped "
                "— try again, or send fewer/lighter files in one message")
    # Never invent a cause: hand back what actually happened.
    return f"the run failed and the reason was: {detail}"


def main() -> None:
    try:
        _main_inner()
    except subprocess.TimeoutExpired as exc:
        # An uncaught timeout crashed the whole drop with a traceback; the broker
        # deserves a sentence, and the engine a parseable line.
        print(json.dumps({"ok": False, "error": failure_message(f"timed out: {exc}")}))
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": failure_message(str(exc))}))


def _main_inner() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    text = payload.get("text", "")
    files = payload.get("files", [])
    actor = payload.get("actor") or "broker"
    ts = payload.get("ts") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    known_slug = payload.get("client_slug") or ""

    # Unpack forwarded email before anything else looks at it, so the reader gets
    # photographs instead of base64 and the crop pass gets real image files.
    stage("start")
    files, email_text, email_notes = expand_emails(
        files, Path(__file__).resolve().parent / "inbox" / "_parts")
    if email_text:
        text = (text + "\n\n" + email_text).strip()

    # locate existing dossier — thread hint first
    dossier, slug = None, known_slug
    if known_slug:
        sp_state = CLIENTS / known_slug / "state.json"
        if sp_state.exists():
            dossier = json.loads(sp_state.read_text(encoding="utf-8"))

    # A text-only instruction posted to the channel has no thread to name its
    # client. Left alone, this once extracted nothing, matched nothing, and
    # CREATED a client called "Unknown Client" with a Drive folder — a correction
    # became a phantom insured. Resolve the client from the words of the message;
    # if that cannot be done unambiguously, ask, never create.
    if dossier is None and not files and carries_instruction(text):
        found = find_client_in_text(text)
        if found and (CLIENTS / found / "state.json").exists():
            slug = found
            dossier = json.loads((CLIENTS / found / "state.json")
                                 .read_text(encoding="utf-8"))
        else:
            print(json.dumps({"ok": False, "error":
                "that reads as a correction, but I can't tell which client it "
                "belongs to — reply in the client's thread or include the "
                "client's name"}))
            return

    # A correction never goes through the full-dossier round trip. Asked to hand
    # back an updated dossier after "the Chevy should be 78,500", the reader also
    # re-valued a Ford at 11223 — the tail of its own VIN — and recomputed the
    # total so the arithmetic still agreed. Targeted edits cannot do that: only the
    # named field on the named row is touched.
    instruction = carries_instruction(text) if dossier is not None else ""
    if instruction and not files:
        data, edit_notes = apply_correction(dossier, text, files)
        op, summary = "correct", f"applied: {text.strip()[:110]}"
        red_flags = []
        if edit_notes:
            _extend_notes(data, edit_notes)
    elif instruction:
        # "Replace other truck" plus attachments: read the material once with no
        # baseline, fold the plain additions in deterministically, then let the
        # instruction do what a blank-filling merge cannot — overwrite and remove.
        fresh = claude_extract(text, files, None)
        fresh.pop("_op", None)
        fresh.pop("_summary", None)
        red_flags = fresh.pop("red_flags", [])
        data = merge_dossier(dossier, fresh)
        _extend_notes(data, fresh.get("_identifier_notes"))
        data, edit_notes, op_summary = apply_instruction(data, text, fresh,
                                                        dossier)
        op = "correct"
        summary = op_summary or f"applied: {instruction}"
        if edit_notes:
            _extend_notes(data, edit_notes)
        _extend_notes(data, [
            f"the drop says “{instruction}”, so it was applied as an instruction "
            f"on top of the merge — check the changes below carefully"])
    else:
        data = claude_extract(text, files, dossier)
        op = data.pop("_op", "create" if dossier is None else "add")
        summary = data.pop("_summary", "")
        red_flags = data.pop("red_flags", [])
        derive_totals(data)
    stage("extraction + identifier passes")
    if email_notes:
        data.setdefault("_identifier_notes", []).extend(email_notes)

    # Loss runs get their own pass: gospel fields verified against the PDF text,
    # the 60-day clock checked, and the newest term promoted into current carrier
    # and expiration — the update JC said "annoys me" when his team skips it.
    lr_files = [f for f in files if str(f).lower().endswith(".pdf")]
    try:
        import lossruns
        lr_files = [f for f in lr_files if lossruns.looks_like_lossrun(Path(f))]
        if lr_files:
            runs_, lr_notes = lossruns.extract(lr_files)
            runs_, lr_changes = lossruns.apply_gospel(data, runs_)
            clock = lossruns._dedupe(lossruns.sixty_day_clock(
                runs_, (data.get("company") or {}).get("policy_effective_date")
                or (data.get("company") or {}).get("current_auto_expires")))
            data.setdefault("_identifier_notes", []).extend(lr_notes + clock)
            data.setdefault("_lr_changes", []).extend(lr_changes)
            stage(f"loss-run gospel pass ({len(runs_)} term(s))")
    except Exception as exc:  # noqa: BLE001
        data.setdefault("_identifier_notes", []).append(
            f"loss-run pass failed ({type(exc).__name__}); the PDFs were still "
            f"read by the general extraction, but gospel checks did not run")

    client = (data.get("company", {}) or {}).get("first_named_insured") \
        or (data.get("company", {}) or {}).get("dba") or "Unknown Client"
    sp = sp_name(client)
    # Anchor the LOCAL folder on the SP code, the same way drive_api anchors the
    # Drive folder. Deriving it from the name every time forked Nora's Towing into
    # two half dossiers in one afternoon — one holding the filled app, the other
    # the changelog and the archived sources — and the Drive upload then looked
    # for the app in the half that did not have it and skipped it in silence.
    # The name wobbles between readings; the SP code did not.
    slug = slug or slug_for_sp_code(sp) or slugify(client)

    # A code already on file wins — changing it would move the client's Drive
    # folder — but a disagreement with JC's book is announced, never absorbed.
    # Our own test files carry BROOK1 where Sierra files BROOK2, and NORAS1
    # where the book says NORAS.
    stored = (dossier or {}).get("sp_code")
    if stored and stored != sp:
        _extend_notes(data, [
            f"SP code on file is {stored}, but Drive says this client is {sp} — "
            f"deliverables are still going out as {stored}; say which is right "
            f"before the packet is submitted"])
        sp = stored

    # IDENTITY MATCH: brokers post to the channel, not in-thread, and names
    # arrive misspelled. Match on hard identifiers (DOT/FEIN/CA#/phone) then on
    # name similarity, against local dossiers AND the Clients/ folders in Drive.
    match_reason = ""
    if dossier is None:
        from client_match import build_index, drive_client_folders, find_match
        index = build_index(drive_client_folders())
        matched_slug, match_reason = find_match(data, index)
        if matched_slug:
            existing = CLIENTS / matched_slug / "state.json"
            slug = matched_slug
            if existing.exists():
                dossier = json.loads(existing.read_text(encoding="utf-8"))
                instruction = carries_instruction(text)
                if instruction and not files:
                    # a text-only instruction: targeted edits, never the
                    # full-dossier rewrite (which once re-valued a truck to the
                    # tail of its own VIN)
                    data, edit_notes2 = apply_correction(dossier, text, files)
                    sp = dossier.get("sp_code") or sp
                    op, summary = "correct", f"applied: {text.strip()[:110]}"
                    if edit_notes2:
                        _extend_notes(data, edit_notes2)
                elif instruction:
                    # "Replace other truck" plus attachments: the material was
                    # already read once, dossier-free. Fold the plain additions
                    # in deterministically, then let the instruction do what a
                    # blank-filling merge cannot — overwrite and remove. (The
                    # old route here re-read all six images against the dossier
                    # — a second multi-minute pass — and the fold still lost
                    # the replacement truck.)
                    fresh = data
                    data = merge_dossier(dossier, fresh)
                    _extend_notes(data, fresh.get("_identifier_notes"))
                    data, edit_notes2, op_summary = apply_instruction(
                        data, text, fresh, dossier)
                    sp = dossier.get("sp_code") or sp
                    op = "correct"
                    summary = op_summary or f"applied: {instruction}"
                    if edit_notes2:
                        _extend_notes(data, edit_notes2)
                    _extend_notes(data, [
                        f"the drop says “{instruction}”, so it was applied as an "
                        f"instruction on top of the merge — check the changes "
                        f"below carefully"])
                else:
                    # Merge in code. Re-reading every attachment against the
                    # dossier doubled the wall clock — the run that lost Capitol
                    # Valley's email spent its budget doing this twice — and asking
                    # a model for "the full updated dossier" gives it room to
                    # quietly drop fields that were already right. Bookkeeping
                    # should be deterministic.
                    fresh_notes = data.get("_identifier_notes") or []
                    data = merge_dossier(dossier, data)
                    _extend_notes(data, fresh_notes)
                    derive_totals(data)
                    op = "add"
                client = (data.get("company", {}) or {}).get("first_named_insured") \
                    or (data.get("company", {}) or {}).get("dba") or client
                # The code on the FILE wins. Recomputing it from the name gave
                # "LAKES1" for a client whose folder is "LAKES", so an addition to
                # Lakeside's policy created a second Drive folder and put the
                # truck schedule in it. One client, one code, forever — it is
                # printed on documents that have already gone to carriers.
                sp = dossier.get("sp_code") or sp_name(client)

    # Progressive precedence runs after every merge path and before the dedupe:
    # a same-truck VIN mismatch collapses in Progressive's favour when the drop
    # carries Progressive paper printing exactly one of the pair's VINs.
    prog_notes = reconcile_progressive_vins(data, progressive_vins(files))
    if prog_notes:
        _extend_notes(data, prog_notes)
        derive_totals(data)

    # The file's own record comes back before anything is written. Every path
    # above can hand `data` to a model, and a model only ever returns the
    # client half — see carry_history for the drop that deleted twenty
    # findings off a live file.
    data = carry_history(dossier, data)

    # Last gate before anything is written: no list carries two rows that say the
    # same thing. Every keying rule above can be defeated by a row with neither a
    # key nor a legible year, and one drop ended with the same truck twice.
    _extend_notes(data, dedupe_lists(data))
    derive_totals(data)

    client_dir = CLIENTS / slug
    client_dir.mkdir(parents=True, exist_ok=True)

    # changelog diff (old dossier vs new)
    prior = {k: v for k, v in (dossier or {}).items() if not k.startswith("_")}
    changes = diff_dossier(prior, data)
    changelog = (dossier or {}).get("_changelog", [])

    # archive source materials into _source (files + the broker's text note).
    # Same document re-sent = same archive entry: the NICO scan arrived three
    # times in one test and _source kept all three under different timestamps,
    # locally AND in Drive. Identity is (original name, byte size) — a re-send
    # is skipped, a genuinely revised file (same name, different bytes) is kept.
    source_dir = client_dir / "_source"
    source_dir.mkdir(exist_ok=True)
    stamp_file = ts.replace(":", "").replace("-", "").replace(" ", "_")
    archived, files_to_upload, reused = archive_sources(files, source_dir,
                                                        stamp_file)
    note_written = False
    if text.strip():
        # The same message replayed — a retry, a re-forwarded email — must not
        # mint a new note file on every pass. The body is compared without its
        # header line, which carries the timestamp and would always differ.
        body_new = text.strip()
        dup = False
        for q in source_dir.glob("*_broker_note.txt"):
            try:
                prev = q.read_text(encoding="utf-8")
            except OSError:
                continue
            if "\n" in prev and prev.split("\n", 1)[1].strip() == body_new:
                dup = True
                break
        if not dup:
            note = source_dir / f"{stamp_file}_broker_note.txt"
            note.write_text(f"[{ts}] {actor}:\n{text}", encoding="utf-8")
            archived.append(note.name)
            note_written = True

    # The SP code is the client's identity and everything downstream names
    # itself from it — the app PDF, the RTS workbook, the Drive folder. Anchor
    # it on the file HERE, with the first write, not later as a side effect of
    # the Drive upload: Shoreline's first drop produced a workbook called
    # "CLIENT CAP RTS supp app" because the vendor build ran in between.
    data["sp_code"] = data.get("sp_code") or sp

    prev_hash = changelog[-1]["hash"] if changelog else GENESIS
    entry = {
        "ts": ts, "actor": actor, "op": op,
        "summary": summary or f"{op} via Slack",
        "source": source_line(archived, reused),
        "changes": changes,
        "prev": prev_hash,
    }
    entry["hash"] = entry_fingerprint(entry, prev_hash)
    changelog.append(entry)
    data["_changelog"] = changelog
    state_file = client_dir / "state.json"
    state_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # fill the app from the merged dossier (strip _changelog for the filler)
    fill_input = client_dir / "fill_input.json"
    fill_input.write_text(json.dumps({k: v for k, v in data.items()
                                      if not k.startswith("_")}, indent=1), encoding="utf-8")
    # UTF-8 explicitly, and never abort on a byte that will not decode. On Windows
    # `text=True` defaults to cp1252, so the first curly quote in a report — or the
    # first client called Núñez Towing — killed the reader thread, left `report`
    # as None, and surfaced as an unrelated "got 'NoneType'". The whole pipeline
    # died on a punctuation mark.
    proc = subprocess.run(
        [sys.executable, str(FILL), "--client", client, "--data", str(fill_input)],
        capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    report = proc.stdout or ""
    if not report.strip():
        raise RuntimeError(f"fill_app produced no report: "
                           f"{(proc.stderr or '')[-400:]}")

    def section(name: str) -> list[str]:
        out, grab = [], False
        for ln in report.splitlines():
            if ln.startswith("## "):
                grab = name.lower() in ln.lower()
                continue
            if grab and ln.strip().startswith("- ") and "none" not in ln.lower():
                out.append(ln.strip()[2:])
        return out

    filled = re.search(r"Filled: (.+)", report)
    auto_flags = section("Consistency red flags")
    missing = section("Missing key fields")
    unanswered = section("Questions the client left blank")
    defaults = section("Applied defaults")
    # Red flags survive the message. The Fowler test drop disclosed an accident
    # settled in cash and never reported to the carrier — the single most
    # valuable thing in that document — and it appeared once in Slack and was
    # gone: nothing wrote it down, so it could never reach the quoting packet.
    # Findings belong to the file, not to a chat scroll.
    keep = [f for f in list(red_flags) + list(auto_flags)
            if f not in (data.get("_red_flags") or [])]
    if keep:
        data.setdefault("_red_flags", []).extend(keep)
        # The state was written before the app was filled, so the flags found in
        # that pass have to be committed here. Written once and left there, the
        # cousin driving uninsured never reached the file at all.
        state_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # What a human has to resolve leads; how the engine read the photographs is
    # filed, not announced. Both are kept in full in state.json.
    #
    # And only what is NEW leads. Lakeside's file carries findings from earlier
    # drops — the overdue Statement of Information, the 24-hour Yelp listing —
    # and re-announcing all of them on every drop is how a message reached
    # fifteen flags: the same five kept coming back next to two new ones.
    prior_notes = set((dossier or {}).get("_identifier_notes") or [])
    prior_flags = set((dossier or {}).get("_red_flags") or [])
    fresh_ids = [n for n in (data.get("_identifier_notes") or [])
                 if n not in prior_notes]
    carried = (len(data.get("_identifier_notes") or []) - len(fresh_ids)
               + len(prior_flags))
    all_flags, filed_notes = triage_flags(
        condense_notes(fresh_ids),
        [f for f in red_flags if f not in prior_flags],
        [f for f in auto_flags if f not in red_flags and f not in prior_flags])
    filed_notes += carried

    # Judgment calls the engine made on its own. These get read back to the
    # broker for a yes/no before they're treated as settled — the engine never
    # silently decides where a file goes or which value wins.
    decisions = []
    if match_reason:
        decisions.append(f"Filed into the existing *{sp}* file — {match_reason}")
    for c in changes:
        if c["from"] not in (None, "", [], {}):
            decisions.append(
                f"Changed *{humanize(c['field'])}*: {c['from']} → *{c['to']}*")
    for d in defaults:
        decisions.append(f"Applied standard answer: {d}")
    # Gospel edits from loss runs are judgment calls too: announced, and anything
    # phrased as QUESTION waits for the broker's answer like every other question.
    for ch in data.pop("_lr_changes", []):
        decisions.append(ch)

    # The second half of phase 1: the vendor app, when the risk qualifies — or
    # when the message asked for it by name, which outranks the rule.
    vendor = build_vendor_apps(data, slug, caption=text)
    rts = vendor["rts"]
    if rts.get("delivered") or rts.get("file"):
        decisions.append(f"Built the RTS/Progressive app — {rts['reason']}")
        if rts.get("overridden"):
            # Built, so it belongs in decisions — but a rule was set aside on a
            # human's word, and that is exactly the kind of thing that must not
            # sit quietly in a list of things that went fine.
            unanswered.append(
                "You asked for the RTS/Progressive app and it is in the folder, "
                "but the routing rule would not have made one for this risk: "
                f"{rts['rule_said']}. Confirm it belongs before this goes out.")
    elif rts.get("error"):
        decisions.append(f"RTS app failed to build: {rts['error']}")
    elif rts["unknown"]:
        # A gap, not a decision — this one has to reach the broker.
        unanswered.append(rts["reason"])
    else:
        decisions.append(f"No RTS app — {rts['reason']}")

    # deliver organized folder to Drive
    out_name = f"{sp} CAP app {datetime.date.today().month}.{datetime.date.today().day}.{str(datetime.date.today().year)[2:]}.pdf"
    folder_link = ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from drive_api import client_folders, upload_to_drive, folder_link as flink
        _, client_fid, source_fid = client_folders(sp, client)
        folder_link = flink(client_fid)
        # Anchor the code on the file the first time a folder is resolved. Left to
        # be recomputed from the name on every drop, one client collected three
        # Drive folders in one day — LAKES, LAKES1 and CLIENT — because the name
        # changed slightly and the arithmetic changed with it.
        if data.get("sp_code") != sp:
            data["sp_code"] = sp
            state_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
        drafts = list(client_dir.glob("*_CAP_app_2026_DRAFT.pdf"))
        if drafts:
            upload_to_drive(str(drafts[0]), out_name, parent_id=client_fid)
        if vendor["rts"].get("file"):
            # rts_fill already names it to M8 doctrine — keep that name.
            rts_name = Path(vendor["rts"]["file"]).name
            upload_to_drive(vendor["rts"]["file"], rts_name, parent_id=client_fid)
            vendor["rts"]["delivered"] = rts_name
        # Human-readable PDF is the ONLY audit artifact that goes to Drive.
        # The verifiable .md (with hashes) stays on the engine, out of the
        # brokers' reach — that's the authoritative integrity copy.
        cl_md = client_dir / "CHANGELOG.md"
        cl_md.write_text(render_changelog(changelog, client, sp), encoding="utf-8")
        try:
            from audit_pdf import render_audit_pdf
            intact, msg = verify_chain(changelog)
            cl_pdf = client_dir / "Change History.pdf"
            render_audit_pdf(changelog, client, sp, intact, msg, str(cl_pdf))
            upload_to_drive(str(cl_pdf), "Change History.pdf", parent_id=client_fid)
        except Exception:  # noqa: BLE001 - PDF is a nicety; .md is the source of truth
            pass
        # source materials this drop — only what the dedupe actually archived,
        # so a re-sent file does not mint another Drive copy either
        for p in files_to_upload:
            upload_to_drive(str(p), f"{stamp_file}_{p.name.split('_',1)[-1]}",
                            parent_id=source_fid)
        if note_written:
            upload_to_drive(str(source_dir / f"{stamp_file}_broker_note.txt"),
                            f"{stamp_file}_broker_note.txt", parent_id=source_fid)
    except Exception as exc:  # noqa: BLE001
        folder_link = f"(Drive upload skipped: {exc})"

    result = {
        "ok": True, "op": op, "client": client, "sp_name": sp, "slug": slug,
        "match_reason": match_reason,
        "output_name": out_name, "folder_link": folder_link,
        "filled_summary": (filled.group(1) if filled else "app filled")
        + f" · {len(data.get('vehicles', []))} vehicles · {len(data.get('drivers', []))} drivers",
        "changes": changes, "red_flags": all_flags, "missing": missing,
        "unanswered": unanswered, "filed_notes": filed_notes, "rts": rts,
        "headline": headline(op, changes, data),
        "summary": summary, "decisions": decisions,
    }
    print(json.dumps(result))


def confirm() -> None:
    """Record a human verdict on the engine's judgment calls.

    stdin: {"slug", "actor", "ts", "verdict": "approved"|"rejected", "note"}
    Appends to the same hash-chained log (append-only: a rejection is recorded,
    never erased) and republishes the readable history to Drive.
    """
    p = json.loads(sys.stdin.read() or "{}")
    slug, actor = p["slug"], p.get("actor", "broker")
    ts = p.get("ts") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    verdict, note = p.get("verdict", "approved"), (p.get("note") or "").strip()

    client_dir = CLIENTS / slug
    state_file = client_dir / "state.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    changelog = data.get("_changelog", [])
    # What is being signed off is the last real CHANGE, not the last sign-off.
    # Taking changelog[-1] blindly produced “Flagged as wrong by Rafael:
    # “Approved by Rafael: “applied: …””” — a review of a review.
    reviewed = next((e["summary"] for e in reversed(changelog)
                     if e.get("op") != "review"), "")

    label = "Approved" if verdict == "approved" else "Flagged as wrong"
    summary = f"{label} by {actor}: “{reviewed}”"
    if note:
        summary += f" — {note}"

    prev_hash = changelog[-1]["hash"] if changelog else GENESIS
    entry = {"ts": ts, "actor": actor, "op": "review", "summary": summary,
             "source": "Slack confirmation", "changes": [], "prev": prev_hash}
    entry["hash"] = entry_fingerprint(entry, prev_hash)
    changelog.append(entry)
    data["_changelog"] = changelog
    state_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    client = (data.get("company", {}) or {}).get("first_named_insured") or slug
    # Same rule as the drop path: the code on the file wins. Recomputed here, a
    # broker's "yes" published the change history into a second Drive folder.
    sp = data.get("sp_code") or sp_name(client)
    out = {"ok": True, "verdict": verdict, "sp_name": sp, "client": client}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from audit_pdf import render_audit_pdf
        from drive_api import client_folders, upload_to_drive
        intact, msg = verify_chain(changelog)
        cl_md = client_dir / "CHANGELOG.md"
        cl_md.write_text(render_changelog(changelog, client, sp), encoding="utf-8")
        cl_pdf = client_dir / "Change History.pdf"
        render_audit_pdf(changelog, client, sp, intact, msg, str(cl_pdf))
        _, client_fid, _ = client_folders(sp, client)
        upload_to_drive(str(cl_pdf), "Change History.pdf", parent_id=client_fid)
    except Exception as exc:  # noqa: BLE001
        out["warning"] = f"log saved locally, Drive update skipped: {exc}"
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        confirm() if "--confirm" in sys.argv else main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(0)
