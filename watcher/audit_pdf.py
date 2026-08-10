"""Render a client's change history as a clean, human-readable PDF.

Turns the machine changelog (dotted field paths + hashes) into plain-English
events a non-technical broker or JC can read at a glance. The hash-chain
verification result becomes a simple green "Verified" / red "Tampered" banner.
"""
from __future__ import annotations

import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#1f2933")
STEEL = colors.HexColor("#52606d")
BLUE = colors.HexColor("#2c5f8a")
GOOD = colors.HexColor("#3f8a5c")
BAD = colors.HexColor("#c0392b")
LINE = colors.HexColor("#dfe3e8")
SOFT = colors.HexColor("#f5f7f9")

# friendly labels for the common field prefixes
FIELD_LABELS = {
    "company.dba": "Doing-business-as (DBA)",
    "company.first_named_insured": "Insured business name",
    "company.fein": "Federal Tax ID (FEIN)",
    "company.entity_type": "Business structure",
    "company.owner_name": "Owner name",
    "company.owner_email": "Owner email",
    "company.contact_name": "Contact name",
    "company.contact_email": "Contact email",
    "company.contact_cell": "Contact cell phone",
    "company.office_phone": "Office phone",
    "company.total_vehicles": "Total number of vehicles",
    "company.total_drivers": "Total number of drivers",
    "company.usdot_number": "US DOT number",
    "company.state_filing_number": "State filing (CA) number",
    "company.mc_number": "MC number",
    "company.mailing_address": "Mailing address",
    "company.location_address": "Location address",
    "company.current_auto_carrier": "Current auto carrier",
    "company.current_auto_expires": "Current policy expiration",
    "company.expiring_premium": "Expiring premium",
    "company.cross_state_lines": "Crosses state lines",
    "company.new_venture": "New venture",
    "coverages.auto_liability": "Auto liability limit",
    "coverages.on_hook": "On-hook / cargo limit",
    "coverages.general_liability": "General liability",
    "ops_details.gross_revenue": "Estimated gross revenue",
    "vehicle_totals.stated_value": "Fleet — total stated value",
    "vehicle_totals.power_units": "Fleet — power units",
    "vehicle_totals.trailers": "Fleet — trailers",
    "location.address": "Yard / location address",
    "overall_description": "Operations description",
}

MONEY_FIELDS = ("premium", "stated_value", "revenue", "value")


def humanize_field(path: str) -> str:
    if path in FIELD_LABELS:
        return FIELD_LABELS[path]
    # vehicles[VIN].field / drivers[NAME].field / loss_runs[YEAR].field / contracts[NAME].field
    m = re.match(r"(vehicles|drivers|loss_runs|contracts)\[([^\]]+)\]\.(.+)", path)
    if m:
        kind, ident, sub = m.groups()
        kind_label = {"vehicles": "Vehicle", "drivers": "Driver",
                      "loss_runs": "Loss run", "contracts": "Contract"}[kind]
        sub_label = sub.replace("_", " ")
        return f"{kind_label} “{ident}” — {sub_label}"
    # revenue_sources.X_pct / operations.X etc.
    if "." in path:
        head, tail = path.split(".", 1)
        head_label = {"revenue_sources": "Revenue source", "operations": "Operation",
                      "goods_hauled": "Goods hauled", "radius": "Radius"}.get(head)
        if head_label:
            return f"{head_label} — {tail.replace('_', ' ').replace(' pct', ' %')}"
    return path.replace("_", " ").replace(".", " — ")


def fmt_val(field: str, v) -> str:
    if v in (None, ""):
        return "(blank)"
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    s = str(v)
    if any(k in field for k in MONEY_FIELDS):
        num = s.replace(",", "").replace("$", "")
        try:
            return "$" + f"{float(num):,.0f}"
        except ValueError:
            return s
    return s


OP_LABEL = {
    "create": ("Created", BLUE),
    "add": ("Information added", GOOD),
    "correct": ("Correction", colors.HexColor("#b7791f")),
}


def render_audit_pdf(entries: list, client: str, sp: str, intact: bool,
                     integrity_msg: str, out_path: str) -> str:
    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("t", parent=styles["Title"], textColor=INK, fontSize=20,
                             spaceAfter=2, alignment=TA_LEFT)
    h_sub = ParagraphStyle("s", parent=styles["Normal"], textColor=STEEL, fontSize=10.5,
                           spaceAfter=14)
    ev_head = ParagraphStyle("eh", parent=styles["Normal"], fontSize=12, textColor=INK,
                             spaceAfter=1, leading=15)
    ev_when = ParagraphStyle("ew", parent=styles["Normal"], fontSize=9.5, textColor=STEEL,
                             spaceAfter=6)
    ev_sum = ParagraphStyle("es", parent=styles["Normal"], fontSize=10.5, textColor=INK,
                            spaceAfter=6, leading=14)
    chg = ParagraphStyle("c", parent=styles["Normal"], fontSize=10, textColor=INK, leading=14)

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            title=f"Change History — {client}")
    flow = []
    flow.append(Paragraph("Change History", h_title))
    flow.append(Paragraph(f"{client} &nbsp;·&nbsp; file {sp}", h_sub))

    # integrity banner
    badge = "&#10003;  VERIFIED — this history has not been altered" if intact \
        else "&#9888;  TAMPERED — this history was changed outside the system"
    bcolor = GOOD if intact else BAD
    banner = Table([[Paragraph(f'<font color="white"><b>{badge}</b></font>',
                               ParagraphStyle("b", fontSize=10.5, leading=14))]],
                   colWidths=[6.9 * inch])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bcolor),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    flow.append(banner)
    flow.append(Spacer(1, 16))

    for e in reversed(entries):  # newest first
        label, lcolor = OP_LABEL.get(e["op"], ("Change", STEEL))
        # nice date: "July 23, 2026 · 4:05 PM"
        when = e["ts"]
        flow.append(Paragraph(
            f'<font color="#{lcolor.hexval()[2:]}"><b>{label}</b></font> '
            f'by <b>{e["actor"]}</b>', ev_head))
        flow.append(Paragraph(when, ev_when))
        if e.get("summary"):
            flow.append(Paragraph(e["summary"], ev_sum))

        rows = []
        for c in e.get("changes", []):
            field = humanize_field(c["field"])
            frm = fmt_val(c["field"], c["from"])
            to = fmt_val(c["field"], c["to"])
            arrow = f'{frm} &nbsp;&rarr;&nbsp; <b>{to}</b>' if frm != "(blank)" \
                else f'<b>{to}</b>'
            rows.append([Paragraph(field, chg), Paragraph(arrow, chg)])
        if rows:
            t = Table(rows, colWidths=[2.9 * inch, 4.0 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            flow.append(t)
        if e.get("source"):
            flow.append(Spacer(1, 3))
            flow.append(Paragraph(
                f'<font size="8" color="#7b8794">Source on file: {e["source"]}</font>',
                styles["Normal"]))
        flow.append(Spacer(1, 16))

    flow.append(Spacer(1, 6))
    tip = entries[-1].get("hash", "?") if entries else "?"
    flow.append(Paragraph(
        f'<font size="7.5" color="#9aa5b1">Tamper-evident record · {len(entries)} '
        f'entries · security seal {tip} · authoritative copy held on the Sierra '
        f'engine.</font>', styles["Normal"]))

    doc.build(flow)
    return out_path
