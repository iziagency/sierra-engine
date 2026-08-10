r"""The six web reports of the quoting packet, captured the way JC captures them.

Sources, taken from his Notion `Quoting packet checklist`:

  website   / facebook / instagram / yelp   -> "copy and paste entity"
  google street report per loc              -> "copy and paste address"
  google overhead report per loc            -> "copy and paste address"

"Copy and paste entity" is the instruction to a human operator, not a mandate to
use a search engine. The application asks the insured for their website,
Instagram and Facebook precisely so the file can be checked against them, so the
declared value is the first choice and a search is only the fallback when the
field is blank. Going through a search engine was how a California tow company
once came back as Brazilian football news.

Two rules learned the hard way:

  * An empty profile IS the finding. Lakeside's Instagram exists with 0 posts and
    0 followers; that goes in the packet exactly as it renders, because "young
    business, no online footprint" is something an underwriter prices.
  * A capture that fails on OUR side never becomes a page. No "capture
    unavailable" placeholder — the operator gets told, the slot stays empty, and
    the checklist reports it as not included.

Street View is aimed at the property by bearing rather than left on whatever the
nearest pano happens to face. On page 29 of the real packet the camera sits on
the road seam and the house is off to the left behind a stitching blur; JC said
on the 7.22 call, "I don't know why they took out the front of the picture."
Aiming it and ringing the parcel fixes his own complaint.

Usage:
  python webrpt.py --client lakeside-towing-llc
  python webrpt.py --client lakeside-towing-llc --only street,overhead
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "app-form" / "clients"
SHOTS = ROOT / "reports" / "captures"
sys.path.insert(0, str(ROOT / "reports"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# Google localises by IP; the packet goes to a US underwriter, so pin the UI.
EN_US = "hl=en&gl=us"

# Text that means the site refused the visit rather than answered it. A refusal
# is an operator problem, never a page in the client's file.
BLOCKED = (
    "you have been blocked", "verify you are human", "are you a robot",
    "unusual traffic", "access denied", "request blocked", "captcha",
    "enable javascript and cookies to continue", "unsupported browser",
)


# ---------------------------------------------------------------- geocoding

def geocode(address: str) -> tuple[float, float] | None:
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q="
           + urllib.parse.quote(address))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SierraPacificQP/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            hits = json.loads(r.read())
        return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
    except Exception:  # noqa: BLE001
        return None


def camera_and_bearing(coords: tuple[float, float]):
    """Where to stand and which way to look: (road_lat, road_lon, bearing) | None.

    Street View drops the camera on the road; left alone it faces whichever way
    the survey car was driving, which is why page 29 of the real packet looks
    down the street with the house off to one side. Standing on the nearest road
    point and aiming across at the parcel is what puts the front of the building
    in the middle of the frame.

    Returns None when the road geometry cannot be resolved — the caller then
    ships an unaimed capture and skips the ring, because a red circle drawn over
    whatever happened to be centred is worse than no circle at all.
    """
    lat, lon = coords
    q = f"""[out:json][timeout:25];
    way(around:60,{lat},{lon})["highway"];
    out geom 40;"""
    try:
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=urllib.parse.urlencode({"data": q}).encode(),
            headers={"User-Agent": "SierraPacificQP/1.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            ways = json.loads(r.read()).get("elements", [])
    except Exception:  # noqa: BLE001
        return None

    named = [w for w in ways if w.get("tags", {}).get("name")] or ways

    # Project onto the road SEGMENTS, not onto their vertices. OSM stores this
    # street with 18 points for its whole length, so the nearest vertex sits ~28 m
    # up the block and the bearing from there to the house runs almost parallel to
    # the kerb — which is exactly how the camera ended up staring down the street.
    # The perpendicular foot puts it directly in front of the address.
    kx = math.cos(math.radians(lat))          # lon degrees -> comparable to lat
    best = None
    for w in named:
        g = w.get("geometry", [])
        for a, b in zip(g, g[1:]):
            ax, ay = (a["lon"] - lon) * kx, a["lat"] - lat
            bx, by = (b["lon"] - lon) * kx, b["lat"] - lat
            dx, dy = bx - ax, by - ay
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
            fx, fy = ax + t * dx, ay + t * dy      # foot, relative to property
            d = fx * fx + fy * fy
            if best is None or d < best[0]:
                best = (d, lat + fy, lon + fx / kx)
    if not best:
        return None
    _, rlat, rlon = best
    dlon = math.radians(lon - rlon)
    y = math.sin(dlon) * math.cos(math.radians(lat))
    x = (math.cos(math.radians(rlat)) * math.sin(math.radians(lat))
         - math.sin(math.radians(rlat)) * math.cos(math.radians(lat)) * math.cos(dlon))
    bearing = int((math.degrees(math.atan2(y, x)) + 360) % 360)
    return rlat, rlon, bearing


# ---------------------------------------------------------------- URL builders

def _site(value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    return v if v.startswith(("http://", "https://")) else "https://" + v.lstrip("/")


def _profile(value: str, host: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if v.startswith(("http://", "https://")):
        return v
    return f"https://www.{host}/{v.lstrip('@/')}"


def _handle_guess(company: str) -> str:
    """`Lakeside Towing LLC` -> `lakesidetowing` — the handle the business almost
    always registers. Used only when the app left the field blank."""
    name = re.sub(r"\b(llc|inc|corp|co|ltd|services?|company)\b", " ",
                  str(company or "").lower())
    return re.sub(r"[^a-z0-9]", "", name)


def build_urls(c: dict, coords, address: str) -> dict[str, tuple[str, str, str]]:
    """key -> (url, page title, attribution). Title/attribution mirror JC's."""
    company = c.get("dba") or c.get("first_named_insured") or ""
    city = ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        city = f"{parts[-3]}, {parts[-2].split()[0] if parts[-2] else ''}".strip(", ")
    year = datetime.date.today().year
    guess = _handle_guess(company)

    urls: dict[str, tuple[str, str, str]] = {}

    site = _site(c.get("website"))
    if site:
        urls["website"] = (site, urllib.parse.urlparse(site).netloc, "")

    fb = _profile(c.get("facebook"), "facebook.com") or (
        f"https://www.facebook.com/{guess}" if guess else "")
    if fb:
        urls["facebook"] = (fb, company, "")

    ig = _profile(c.get("instagram"), "instagram.com") or (
        f"https://www.instagram.com/{guess}/" if guess else "")
    if ig:
        urls["instagram"] = (ig, company, "")

    if company:
        urls["yelp"] = (
            "https://www.yelp.com/search?find_desc="
            f"{urllib.parse.quote(company)}&find_loc={urllib.parse.quote(city or 'CA')}",
            company, "")

    if coords:
        lat, lon = coords
        street_line = parts[0] if parts else address
        aim = camera_and_bearing(coords)
        if aim:
            clat, clon, heading = aim
            # `@lat,lon,3a,<fov>y,<heading>h,<pitch>t/data=!3m1!1e1` is the form
            # Google itself emits when you share a Street View. The `api=1
            # &map_action=pano&viewpoint=` form silently drops the heading and
            # leaves the camera pointing down the road.
            urls["street"] = (
                f"https://www.google.com/maps/@{clat},{clon},3a,78y,"
                f"{heading}h,88t/data=!3m1!1e1?{EN_US}",
                street_line, f"© {year} Google")
        else:
            urls["street"] = (
                f"https://www.google.com/maps/@{lat},{lon},3a,78y,0h,88t"
                f"/data=!3m1!1e1?{EN_US}",
                street_line, f"© {year} Google")
        urls["overhead"] = (
            f"https://www.google.com/maps/@{lat},{lon},19z/data=!3m1!1e3?{EN_US}",
            street_line, f"Imagery © {year} Google, Maxar Technologies")
        urls["_aimed"] = ("yes" if aim else "no", "", "")
    return urls


# ---------------------------------------------------------------- capture

def capture(url: str, png: Path, full_page: bool, mark: bool) -> tuple[str, str]:
    """Returns (page text, error). Never raises for a site's own behaviour."""
    from playwright.sync_api import sync_playwright
    png.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
            ctx = b.new_context(viewport={"width": 1280, "height": 950},
                                user_agent=UA, locale="en-US",
                                timezone_id="America/Los_Angeles")
            pg = ctx.new_page()
            pg.goto(url, timeout=75_000, wait_until="domcontentloaded")
            pg.wait_for_timeout(6_500 if "google.com/maps" in url else 3_500)
            _dismiss_consent(pg)
            if "yelp.com/search" in url:
                try:
                    biz = pg.locator('a[href*="/biz/"]').first
                    href = biz.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://www.yelp.com" + href
                    if "/biz/" in href:
                        pg.goto(href.split("?")[0], timeout=60_000,
                                wait_until="domcontentloaded")
                        pg.wait_for_timeout(3_000)
                except Exception:  # noqa: BLE001 - results page is the fallback
                    pass
            if "google.com/maps" in url:
                _strip_map_chrome(pg)
                pg.wait_for_timeout(1_200)
            if full_page:
                pg.mouse.wheel(0, 2200)
                pg.wait_for_timeout(1_800)
                pg.mouse.wheel(0, -4000)
                pg.wait_for_timeout(900)
            pg.wait_for_timeout(1_500)      # let late bot-walls finish rendering
            # Read EVERY frame: Imperva-style walls render their "You have been
            # blocked" inside an iframe, so the main frame reads clean and a block
            # page sails through a body-only check — it happened twice with Yelp.
            parts = []
            for fr in pg.frames:
                try:
                    parts.append(fr.inner_text("body"))
                except Exception:  # noqa: BLE001
                    continue
            text = " ".join(parts)[:8000]
            clip = None
            if "google.com/maps" in url:
                # Crop to the map canvas. JC's page 29 is imagery and nothing else;
                # cropping removes the side rail without hiding any of the picture,
                # and survives Google renaming their classes.
                clip = pg.evaluate("""() => {
                  const c = document.querySelector('canvas.widget-scene-canvas')
                         || document.querySelector('canvas');
                  if (!c) return null;
                  const r = c.getBoundingClientRect();
                  if (r.width < 400 || r.height < 300) return null;
                  return {x: Math.max(0, r.x), y: Math.max(0, r.y),
                          width: Math.min(r.width, innerWidth - Math.max(0, r.x)),
                          height: Math.min(r.height, innerHeight - Math.max(0, r.y))};
                }""")
            pg.screenshot(path=str(png), full_page=full_page, clip=clip)
            ctx.close()
            b.close()
        if mark:
            ring_center(png)
        return text, ""
    except Exception as exc:  # noqa: BLE001
        return "", f"{type(exc).__name__}: {str(exc)[:130]}"


def _dismiss_consent(pg) -> None:
    """Decline non-essential cookies where a banner covers the capture."""
    for label in ("Reject all", "Decline", "Only necessary", "Rechazar todo"):
        try:
            btn = pg.get_by_role("button", name=label)
            if btn.count():
                btn.first.click(timeout=2_500)
                pg.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue


def _strip_map_chrome(pg) -> None:
    """Hide our browser's map controls, keep Google's own imagery labels.

    JC's page 29 has no search box, no share button and no zoom controls — his
    capture came off a cleaner surface. What it does keep is the location card,
    the "Google Maps" watermark and the "Image capture: <date>" line, so those
    stay: they are the provenance of the photograph, and an underwriter checking
    a file should be able to see when the imagery was taken.
    """
    # Class names read off the live DOM rather than guessed: `.Owrmqf` is the
    # search + Directions block at the top-left, `.lSDxNd`/`.pzfvzf` its buttons.
    # The location card, the "Google Maps" watermark and the "Image capture" line
    # are deliberately left alone — they are the provenance of the photograph.
    pg.add_style_tag(content="""
      .Owrmqf, .AJQtp, .hzegWb, .NaMBUd, .lSDxNd, .pzfvzf, .SxXeRb,
      #omnibox-container, #omnibox, #assistive-chips, #vasquette, #gb,
      #minimap, .app-viewcard-strip, .app-bottom-content-anchor,
      div[role="tooltip"], [aria-label="Zoom in"], [aria-label="Zoom out"],
      [aria-label="Reset the view"], [aria-label="Show Your Location"],
      [aria-label="Share"], [aria-label="Close"], [aria-label="Directions"],
      [aria-label="Rotate the view clockwise"], [aria-label="Rotate the view"],
      [jsaction*="compass"], [jsaction*="minimap"],
      [aria-label="Saved"], [aria-label="Recents"], [aria-label="Layers"],
      [aria-label="Menu"], [aria-label="Get the app"], [aria-label="Collapse side panel"],
      [jsaction*="categoricalsearch"], [aria-label="Restaurants"], [aria-label="Hotels"],
      [aria-label="Things to do"], [aria-label="Museums"], [aria-label="Transit"],
      [aria-label="Pharmacies"], [aria-label="ATMs"], [aria-label="Next"] {
        display: none !important; visibility: hidden !important;
      }
    """)
    # Google slots promotional cards into the map ("Check out Dua Lipa's Los Angeles
    # list") — an advertisement has no business in an underwriting file. Match on
    # the copy rather than a class name, and only remove small floating boxes so the
    # imagery itself can never be the thing that disappears.
    pg.evaluate("""() => {
      const junk = /^(check out|try it now|new on google|explore with|sponsored)/i;
      // Category chips and the side rail are buttons carrying only these labels.
      const chip = /^(restaurants|hotels|things to do|museums|transit|pharmacies|atms|coffee|gas|groceries|saved|recents|get app|layers|menu)$/i;
      for (const el of document.querySelectorAll('div, span, a, button')) {
        const txt = (el.innerText || '').trim();
        if (!txt || txt.length > 140) continue;
        if (!junk.test(txt) && !chip.test(txt)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.width < 380 && r.height > 0 && r.height < 170
            && !el.querySelector('canvas')) el.style.display = 'none';
      }
      // the row that held the chips often keeps its own background
      for (const el of document.querySelectorAll('div')) {
        const r = el.getBoundingClientRect();
        if (r.top > 120 && r.top < 200 && r.height < 60 && r.width > 250
            && !(el.innerText || '').trim() && !el.querySelector('canvas'))
          el.style.display = 'none';
      }
    }""")
    # The inset minimap is the one control CSS above does not reach. Hide only the
    # element that owns its "Expand" button, and only if it is small — anything
    # large enough to be the panorama itself is left alone. Hiding by position
    # instead once blanked the whole pano canvas and produced a black page.
    pg.evaluate("""() => {
      const btn = [...document.querySelectorAll('button, div[role="button"]')]
        .find(b => /^expand$/i.test((b.innerText || '').trim()));
      if (!btn) return;
      for (let el = btn; el && el !== document.body; el = el.parentElement) {
        const r = el.getBoundingClientRect();
        if (r.width > 90 && r.width < 320 && r.height > 40 && r.height < 240
            && !el.querySelector('canvas.widget-scene-canvas')) {
          el.style.display = 'none';
          return;
        }
      }
    }""")


def ring_center(png: Path) -> None:
    """Ring the parcel at the centre of the frame.

    Not JC's own convention — he ships the raw capture — but he complained on
    camera that the property is hard to pick out, so the ring answers that
    without hiding any of the original image.
    """
    from PIL import Image, ImageDraw
    with Image.open(png) as im:
        im = im.convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        cx, cy = im.width // 2, int(im.height * 0.52)
        r = int(min(im.width, im.height) * 0.17)
        for i, w in ((0, 7), (1, 3)):
            col = (255, 255, 255, 200) if i else (214, 46, 38, 255)
            d.ellipse([cx - r - i * 4, cy - r - i * 4, cx + r + i * 4, cy + r + i * 4],
                      outline=col, width=w)
        label = "INSURED LOCATION"
        tw = d.textlength(label)
        bx, by = cx - tw / 2 - 9, cy - r - 34
        d.rectangle([bx, by, bx + tw + 18, by + 21], fill=(214, 46, 38, 235))
        d.text((bx + 9, by + 5), label, fill=(255, 255, 255, 255))
        im.save(png)


# ---------------------------------------------------------------- run

# checklist row label -> (order in the packet, filename slug, full-page capture)
REPORTS = {
    "website":   ("Website report", "website", True),
    "facebook":  ("Facebook report", "facebook", True),
    "instagram": ("Instagram report", "instagram", True),
    "yelp":      ("Yelp report", "yelp", True),
    "street":    ("Google street report per loc", "street", False),
    "overhead":  ("Google overhead report per loc", "overhead", False),
}
ORDER = ["website", "facebook", "instagram", "yelp", "street", "overhead"]


def run(slug: str, only: list[str] | None = None) -> dict:
    from pagebuild import capture_to_pdf

    folder = CLIENTS / slug
    dossier = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    c = dossier.get("company", {}) or {}
    sp = dossier.get("sp_code") or "CLIENT"
    address = c.get("location_address") or c.get("mailing_address") or ""
    keys = [k for k in ORDER if not only or k in only]

    coords = geocode(address) if {"street", "overhead"} & set(keys) else None
    stamp = _m8(datetime.date.today())
    out = ROOT / "reports" / "out" / slug
    out.mkdir(parents=True, exist_ok=True)

    urls = build_urls(c, coords, address)
    # A ring is a claim about where the property is. Only draw it when the camera
    # was actually aimed from the road at the parcel; otherwise the centre of the
    # frame is arbitrary and the circle would be asserting something false.
    aimed = urls.pop("_aimed", ("no", "", ""))[0] == "yes"
    made, problems, notes = {}, [], {}

    for key in keys:
        label, fslug, full = REPORTS[key]
        if key not in urls:
            problems.append(f"{label}: nothing to capture — the app declares no "
                            f"{'address' if key in ('street','overhead') else 'entity'}")
            continue
        url, title, attrib = urls[key]
        png = SHOTS / f"{sp}_{key}.png"
        text, err = capture(url, png, full_page=full,
                            mark=(aimed and key in ("street", "overhead")))

        low = (text or "").lower()
        hit = next((m for m in BLOCKED if m in low), "")
        irrelevant = ""
        entity = str(c.get("dba") or c.get("first_named_insured") or "")
        if key in ("website", "facebook", "instagram", "yelp") and low:
            import re as _re
            words = {w for w in _re.sub(r"[^a-z0-9 ]", " ", entity.lower()).split()
                     if len(w) >= 4 and w not in ("towing", "recovery", "transport",
                                                  "trucking", "service", "services")}
            if words and not any(w in low for w in words):
                irrelevant = (f"the page never mentions "
                              f"{'/'.join(sorted(words))} — wrong page or a bot wall")
        if err or not png.exists() or hit or irrelevant:
            reason = err or irrelevant or f"the site served a block page ({hit!r})"
            problems.append(f"{label}: {reason} — NOT included; capture it by hand "
                            f"from a normal browser")
            continue

        pdf = out / f"{sp} CAP {fslug} report {stamp}.pdf"
        pages = capture_to_pdf(png, pdf, title, attrib)
        made[key] = {"label": label, "pdf": str(pdf), "pages": pages, "url": url}
        notes[key] = _read_finding(key, text, c)
        if key == "street" and not aimed:
            problems.append("Google street report per loc: captured, but the road "
                            "geometry could not be resolved, so the camera is not "
                            "aimed and the parcel is NOT ringed — check the framing "
                            "by hand before this goes out")

    # Merge, never overwrite. A `--only street` re-run must not erase the finding
    # that Instagram is empty; the record of this client's web presence is
    # cumulative and the compiler reads it to fill the checklist page.
    rec = {"retrieved": datetime.datetime.now().isoformat(timespec="seconds"),
           "made": {}, "findings": {}, "problems": []}
    prior = out / "web_reports.json"
    if prior.exists():
        try:
            old = json.loads(prior.read_text(encoding="utf-8"))
            rec["made"] = old.get("made", {})
            rec["findings"] = old.get("findings", {})
            rec["problems"] = [p for p in old.get("problems", [])
                               if not any(p.startswith(REPORTS[k][0]) for k in keys)]
        except Exception:  # noqa: BLE001
            pass
    rec["made"].update(made)
    rec["findings"].update({k: v for k, v in notes.items() if v})
    rec["problems"] = rec["problems"] + problems
    prior.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"sp": sp, "made": made, "problems": problems, "findings": notes,
            "coords": coords, "out": out}


def _read_finding(key: str, text: str, c: dict) -> str:
    """One line of what the page actually says, as a question for the broker.

    Never an answer and never a silent correction — the comparator's whole job
    is to hand the broker something to ask the insured.
    """
    t = re.sub(r"\s+", " ", text or "")
    if key == "instagram":
        m = re.search(r"([\d,.KMk]+)\s*posts?", t)
        if m and m.group(1).strip("0.,") == "":
            return ("Instagram profile exists but is empty (0 posts) — worth noting "
                    "as a young or low-visibility operation.")
    if key == "facebook":
        m = re.search(r"[Oo]perating since (\d{4})", t)
        if m:
            since = int(m.group(1))
            yrs = c.get("years_in_business")
            implied = datetime.date.today().year - since
            if yrs and str(yrs).isdigit() and abs(int(yrs) - implied) > 1:
                return (f"Facebook says “operating since {since}” (≈{implied} yrs) "
                        f"but the app states {yrs} years in business — which is right?")
            return f"Facebook states “operating since {since}”, consistent with the app."
        m = re.search(r"(\d[\d,]*)\s*followers", t)
        if m:
            return f"Facebook page has {m.group(1)} followers."
    if key == "website":
        zips = set(re.findall(r"\b9\d{4}\b", t))
        app_zip = re.search(r"\b9\d{4}\b", c.get("location_address") or "")
        if zips and app_zip and app_zip.group(0) not in zips:
            return (f"Website shows ZIP {', '.join(sorted(zips))} but the app location "
                    f"is {app_zip.group(0)} — confirm the operating address.")
    if key == "yelp":
        if "not rated" in t.lower() or "0 reviews" in t.lower():
            return "No Yelp rating on file."
    if key in ("street", "overhead"):
        # Google labels the pano with the nearest surveyed address, which is not
        # always the insured's number. Say so rather than let the reader assume
        # the photograph is of the address printed above it.
        m = re.search(r"(\d{2,6})\s+[A-Z][A-Za-z.'\- ]{2,30}(?:Rd|Road|St|Street|Ave"
                      r"|Avenue|Blvd|Dr|Drive|Way|Ln|Lane|Ct|Pl|Hwy)", t)
        app_no = re.match(r"\s*(\d{2,6})", c.get("location_address") or "")
        if m and app_no and m.group(1) != app_no.group(1):
            return (f"Google labels this panorama “{m.group(0)}” while the app "
                    f"location is number {app_no.group(1)} — the imagery is of the "
                    f"nearest surveyed point on the block; confirm the parcel.")
    return ""


def _m8(d: datetime.date) -> str:
    return f"{d.month}.{d.day}.{str(d.year)[2:]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--only", help="comma list: " + ",".join(ORDER))
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",")] if args.only else None

    r = run(args.client, only)
    print(f"web reports — {r['sp']}"
          + (f"  (geocoded {r['coords'][0]:.5f},{r['coords'][1]:.5f})" if r["coords"] else ""))
    for key in ORDER:
        m = r["made"].get(key)
        if m:
            print(f"  [x] {m['label']:32s} {m['pages']}p  {Path(m['pdf']).name}")
            if r["findings"].get(key):
                print(f"      → {r['findings'][key]}")
    for p in r["problems"]:
        print(f"  [ ] {p}")


if __name__ == "__main__":
    main()
