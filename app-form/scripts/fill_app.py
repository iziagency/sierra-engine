"""Fill the CAP application template from a semantic client-data JSON.

Usage:
    python fill_app.py --client "All Star Towing" --new     # live call: open a blank draft to type into
    python fill_app.py --client "All Star Towing" --sync    # pull typed values back in + gap report
    python fill_app.py --client "All Star Towing" --data new_info.json [--no-defaults]
    python fill_app.py --client "All Star Towing"           # refill from saved state

Each client has a state file (clients/<slug>/state.json) that accumulates data
across passes: every --data run deep-merges into it (new values win, list items
are matched by VIN / name / year so re-sending a truck updates instead of
duplicating). The PDF is always regenerated from the full state, so partial
info arriving over days is the normal workflow, not a special case.

Outputs into clients/<slug>/:
    state.json                      accumulated data
    <slug>_CAP_app_2026_DRAFT.pdf   filled, still-editable form
    report.md                       filled/missing/defaults/warnings
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy

import fitz

ROOT = r"C:\dev\sierra-pacific\app-form"
TEMPLATE = os.path.join(ROOT, "dist", "CAP_app_2026_fillable.pdf")
DEFAULTS = os.path.join(ROOT, "config", "defaults.json")
CLIENTS = os.path.join(ROOT, "clients")

sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "shared"))
import formatting  # noqa: E402 - needs sys.path set up first

# ---------------------------------------------------------------- mappings

TEXT_MAP = {
    "sp_policy_code": "sp_policy_code",
    "source_code": "p1_source_code",
    "insured_signature": "p9_insured_name_signature_date",
    "overall_description": "p4_overall_description_of_company_operations",
    "other_coverages": "p2_other_coverages_requested",
    "revenue_other_desc": "p2_describe_other_revenue_sources",
    "operations_other_desc": "p3_describe_other_operations",
    "goods_other_desc": "p3_describe_other_good_hauled",
    "other_employees_count": "p7_total_number_of_other_employees",
    "company.dba": "p1_doing_business_as_or_dba",
    "company.first_named_insured": "p1_first_named_insured_on_filing",
    "company.fein": "p1_fein_or_social_security_number",
    "company.owner_name": "p1_owner_name",
    "company.owner_email": "p1_owner_email",
    "company.contact_name": "p1_contact_name",
    "company.contact_email": "p1_contact_email",
    "company.contact_cell": "p1_contact_cell_phone",
    "company.contact_title": "p1_contact_title",
    "company.office_phone": "p1_office_phone",
    "company.total_vehicles": "total_vehicles",
    "company.total_drivers": "total_drivers",
    "company.state_filing_number": "p1_state_filing_number",
    "company.usdot_number": "p1_us_dot_number",
    "company.mc_number": "p1_mc_number",
    "company.mailing_address": "p1_mailing_full_address",
    "company.location_address": "p1_location_full_address",
    "company.commercial_locations_count": "p1_total_of_commercial_locations",
    "company.website": "p1_website_address",
    "company.instagram": "p1_instagram",
    "company.facebook": "p1_facebook",
    "company.current_auto_carrier": "p1_current_auto_insurance_carrier",
    "company.current_auto_expires": "p1_current_auto_policy_expires",
    "company.expiring_premium": "p1_expiring_premium",
    "company.years_with_auto_insurance": "p1_years_with_auto_insurance",
    "company.current_wc_carrier": "p1_current_workers_comp_carrier",
    "company.current_wc_expires": "p1_current_wc_policy_expires",
    "company.dash_cameras_brand": "p1_vehicle_dash_cameras_brand",
    "company.telematics_brand": "p1_vehicle_telematics_brand",
    "company.how_found_sierra": "p1_how_did_you_find_sierra",
    "ops_details.gross_revenue": "p4_1_year_estimated_gross_revenue",
    "location.address": "p8_location_full_address",
    "location.commercial_location_number": "p8_commercial_location_number",
    "location.max_value_per_vehicle": "p8_maximum_value_per_vehicle",
    "location.avg_value_per_vehicle": "p8_average_value_per_vehicle",
    "location.avg_vehicles_stored": "p8_average_of_vehicles_stored",
    "location.max_vehicles_stored": "p8_max_of_vehicles_stored",
    "location.outdoor_sq_footage": "p8_outdoor_storage_sq_footage",
    "location.indoor_sq_footage": "p8_indoor_storage_square_footage",
    "location.office_sq_footage": "p8_office_square_footage",
    "location.details": "p8_location_details",
    "vehicle_totals.stated_value": "veh_total_value",
    "vehicle_totals.power_units": "veh_total_power",
    "vehicle_totals.trailers": "veh_total_trailers",
}

# TEXT_MAP fields whose stored value needs a transform before it goes on the
# page - JC's 7.27 call: dates M.D.YY, phones dotted, no cosmetic dashes on
# an identifier like the CA state filing number.
PHONE_FIELDS = {"p1_contact_cell_phone", "p1_office_phone"}
DATE_FIELDS = {"p1_current_auto_policy_expires", "p1_current_wc_policy_expires"}
DASH_STRIP_FIELDS = {"p1_state_filing_number"}

# bool -> <base>_yes / <base>_no, with optional numeric companion
YESNO_MAP = {
    "company.cross_state_lines": ("p1_cross_state_lines", None),
    "company.home_based": ("p1_home_based_business", None),
    "company.new_venture": ("p1_new_venture", None),
    "company.dash_cameras": ("p1_vehicle_dash_cameras", None),
    "company.telematics": ("p1_vehicle_telematics", None),
    "ops_details.allow_passengers": ("p4_allow_passengers_during_tow", "pct"),
    "ops_details.transportation_plates": ("p4_transportation_plates", "num"),
    "ops_details.repossessed_plates": ("p4_repossessed_plates", "num"),
    "ops_details.hazardous_cargo_ever": ("p4_hazardous_cargo_ever_hauled", None),
    "ops_details.safety_chains_always": ("p4_safety_chains_always_used", None),
    "ops_details.written_vehicle_maintenance": ("p4_written_vehicle_maintenance", None),
    "ops_details.written_safety_program": ("p4_written_safety_program", None),
    "ops_details.q4_10": ("p4_q4_10", None),
    "location.home_based": ("p8_home_based_business", None),
    "location.own_building": ("p8_own_building_at_location", "num"),
}

# dict sections of no/yes+pct rows: semantic key -> field base
PCT_SECTIONS = {
    "revenue_sources": {
        "private_party_incoming_calls": "p2_private_party_incoming_calls",
        "motor_clubs": "p2_motor_clubs",
        "dealers_to_from_auctions": "p2_dealers_to_from_auctions",
        "salvage_hauling": "p2_salvage_hauling",
        "police_rotations": "p2_police_rotations",
        "impounds": "p2_impounds",
        "logistics_companies": "p2_logistics_companies",
        "freight_brokers": "p2_freight_brokers",
        "other": "p2_other_sources",
    },
    "operations": {
        "tow_disabled_autos": "p3_tow_disabled_autos",
        "roadside_assistance": "p3_roadside_assistance_work",
        "private_property_impounds": "p3_private_property_impounds",
        "lien_sales": "p3_lien_sales",
        "police_impounds": "p3_police_impounds",
        "accident_recovery": "p3_accident_recovery",
        "used_auto_hauling": "p3_used_auto_hauling",
        "salvage_auto_hauling": "p3_salvage_auto_hauling",
        "new_auto_hauling": "p3_new_auto_hauling",
        "hazmat_hauling": "p3_hazmat_hauling",
        "towing_not_for_hire": "p3_towing_not_for_hire",
        "repo_work": "p3_any_repo_work",
        "garage_operations": "p3_garage_operations_work",
        "mobile_auto_repair": "p3_mobile_auto_repair_work",
        "general_freight_hauling": "p3_general_freight_hauling",
        "contractor_operations": "p3_contractor_operations",
        "refrigerated_produce_hauling": "p3_refrigerated_produce_hauling",
        "non_refrigerated_produce_hauling": "p3_non_refer_produce_hauling",
        "other": "p3_other_operations",
    },
    "goods_hauled": {
        "private_passenger_vehicles": "p3_private_passenger_vehicles",
        "trucks_10k_gvw": "p3_trucks_10k_gvw",
        "motorcycles": "p3_motorcycles",
        "watercrafts": "p3_watercrafts",
        "refrigerated_produce": "p3_refrigerated_produce",
        "non_refrigerated_produce": "p3_non_refrigerated_produce",
        "general_dry_goods": "p3_general_dry_goods",
        "hazmat_goods": "p3_hazmat_goods",
        "other": "p3_other_goods_hauled",
    },
    "radius": {
        "lt50": "p4_50_miles",
        "51_300": "p4_51_300_miles",
        "301_500": "p4_301_500_miles",
        "501_1000": "p4_501_1_000_miles",
        "1000_2500": "p4_1_000_2_500_miles",
        "2501_5000": "p4_2_501_5_000_miles",
        "5000_plus": "p4_5_000_miles",
    },
}

# pick-one groups; value not in opts -> "other" checkbox + companion text
CHOICE_MAP = {
    "company.entity_type": {"base": "p1_company", "opts": ["sole", "corp", "llc"],
                            "alias": {"sole proprietor": "sole", "sole_proprietor": "sole",
                                      "corporation": "corp", "inc": "corp"}},
    "company.language": {"base": "p1_language", "opts": ["english", "spanish"],
                         "alias": {"en": "english", "es": "spanish", "espanol": "spanish"}},
    "ops_details.hours": {"base": "p4_hours_of_operations", "opts": ["lt12", "h12plus"],
                          "alias": {"<12": "lt12", "12+": "h12plus", "12plus": "h12plus",
                                    "24/7": "h12plus", "24hr": "h12plus"}},
    "coverages.auto_liability": {"base": "p2_auto_liability_limit",
                                 "opts": ["none", "1m", "750k", "500k"], "other": "amt"},
    "coverages.total_stated_value": {"base": "p2_total_stated_value_of_all_vehicles",
                                     "opts": ["none"], "other": "amt"},
    "coverages.on_hook": {"base": "p2_on_hook_or_in_tow_or_cargo_limit",
                          "opts": ["none", "100k", "50k", "25k"], "other": "amt"},
    "coverages.general_liability": {"base": "p2_general_or_garage_liability",
                                    "opts": ["none", "1m"], "other": "amt"},
    "coverages.garage_keepers": {"base": "p2_garage_keepers_legal_liability",
                                 "opts": ["none", "150k", "100k"], "other": "amt"},
    "coverages.building_property": {"base": "p2_building_property_stated_value",
                                    "opts": ["none"], "other": "amt"},
    "coverages.business_personal_property": {"base": "p2_business_personal_property_value",
                                             "opts": ["none"], "other": "amt"},
    "coverages.inland_marine": {"base": "p2_inland_marine_total_stated_value",
                                "opts": ["none"], "other": "amt"},
    "coverages.umbrella": {"base": "p2_umbrella_coverage_limit",
                           "opts": ["none"], "other": "amt"},
    "location.description": {"base": "p8_location_description",
                             "opts": ["none", "storage_lot"], "other": "desc",
                             "alias": {"storage lot": "storage_lot", "no_commercial": "none"}},
    "location.garage_keepers": {"base": "p8_garage_keepers_legal_liability",
                                "opts": ["none", "150k", "100k"], "other": "amt"},
    "location.building_property": {"base": "p8_building_property_stated_value",
                                   "opts": ["none"], "other": "amt"},
    "location.business_personal_property": {"base": "p8_business_personal_property_value",
                                            "opts": ["none"], "other": "amt"},
    "location.keys_policy": {"base": "p8_policy_regarding_handling_of_customer_keys",
                             "opts": ["lockbox"], "other": "desc",
                             "alias": {"lockbox only": "lockbox", "lockbox_only": "lockbox"}},
    "location.property_return_policy": {"base": "p8_policy_regarding_returning_personal_property",
                                        "opts": ["proper_id"], "other": "desc",
                                        "alias": {"proper id": "proper_id", "id": "proper_id"}},
}

# N/A / No / Yes-with-detail rows
TRIPLE_MAP = {
    "location.dogs": ("p8_dogs_at_location", "breed"),
    "location.video_recording": ("p8_video_recording_at_location", "provider"),
    "location.central_alarm": ("p8_central_alarm_at_location", "provider"),
}

# several boxes may apply at once
MULTI_MAP = {
    "location.weapons_policy": ("p8_policy_regarding_handling_of_weapons_or_drugs",
                                {"proper_id", "notify_police"}),
    "location.relinquish_policy": ("p8_policy_regarding_relinquishing_vehicles",
                                   {"proper_id", "registration_verified"}),
}

LR_BLOCKS = ["lr2025", "lr2024", "lr2023", "lr2021", "lr2021b"]
LR_PRINTED_YEARS = [2025, 2024, 2023, 2021, 2021]

KEY_FIELDS = [  # reported as missing when empty - the "can we submit?" shortlist
    "company.first_named_insured", "company.fein", "company.entity_type",
    "company.contact_cell", "company.mailing_address", "company.usdot_number",
    "company.total_vehicles", "company.total_drivers",
    "coverages.auto_liability", "ops_details.gross_revenue", "radius",
    "vehicles", "drivers", "loss_runs", "location.address",
]

# raw AcroForm equivalents: a key field also counts as present when the broker
# typed it straight into the draft PDF (captured via --sync into state["fields"])
RAW_KEY_EQUIV = {
    "company.first_named_insured": ["p1_first_named_insured_on_filing"],
    "company.fein": ["p1_fein_or_social_security_number"],
    "company.entity_type": ["p1_company_sole", "p1_company_corp", "p1_company_llc"],
    "company.contact_cell": ["p1_contact_cell_phone"],
    "company.mailing_address": ["p1_mailing_full_address"],
    "company.usdot_number": ["p1_us_dot_number"],
    "company.total_vehicles": ["total_vehicles"],
    "company.total_drivers": ["total_drivers"],
    "coverages.auto_liability": [
        "p2_auto_liability_limit_none", "p2_auto_liability_limit_1m",
        "p2_auto_liability_limit_750k", "p2_auto_liability_limit_500k",
        "p2_auto_liability_limit_other",
    ],
    "ops_details.gross_revenue": ["p4_1_year_estimated_gross_revenue"],
    "radius": [
        "p4_50_miles_yes", "p4_51_300_miles_yes", "p4_301_500_miles_yes",
        "p4_501_1_000_miles_yes", "p4_1_000_2_500_miles_yes",
        "p4_2_501_5_000_miles_yes", "p4_5_000_miles_yes",
    ],
    "vehicles": ["p5_veh01_desc"],
    "drivers": ["p7_drv01_name"],
    "loss_runs": ["p9_lr2025_carrier", "p9_lr2025_no_policy"],
    "location.address": ["p8_location_full_address"],
}

MAX_VEHICLES, MAX_DRIVERS, MAX_CONTRACTS = 20, 21, 4

# ---------------------------------------------------------------- helpers


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_path(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def money(v) -> str:
    """Format a number with thousands separators; pass strings through."""
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        try:
            v = float(s)
        except ValueError:
            return v
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def join_parts(parts, sep=" / ") -> str:
    return sep.join(str(p).strip() for p in parts if p not in (None, "", []))


def deep_merge(base, new, list_keys=("vin", "name", "year")):
    """new wins; dicts merge recursively; lists merge by identifying key."""
    if isinstance(base, dict) and isinstance(new, dict):
        out = dict(base)
        for k, v in new.items():
            out[k] = deep_merge(base.get(k), v, list_keys) if k in base else v
        return out
    if isinstance(base, list) and isinstance(new, list):
        out = list(base)
        for item in new:
            key = None
            if isinstance(item, dict):
                key = next((k for k in list_keys if item.get(k)), None)
            if key:
                for i, old in enumerate(out):
                    if isinstance(old, dict) and str(old.get(key, "")).lower() == str(item[key]).lower():
                        out[i] = deep_merge(old, item, list_keys)
                        break
                else:
                    out.append(item)
            else:
                out.append(item)
        return out
    return new if new not in (None, "") else base


# ---------------------------------------------------------------- filler


class Filler:
    def __init__(self, data: dict):
        self.data = data
        self.values: dict[str, object] = {}   # field name -> value
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def set(self, field: str, value):
        # A model or a broker sometimes answers "unknown" / "N/A" rather than
        # actually leaving the field empty - JC's rule is the same either
        # way: blank beats a printed placeholder that reads like a real
        # answer ("just leave it blank").
        value = formatting.blank_if_unknown(value)
        if value in (None, ""):
            return
        self.values[field] = value

    def check(self, field: str):
        self.values[field] = True

    # -- generic handlers --
    def yesno(self, base: str, value, companion_suffix=None):
        comp = None
        if isinstance(value, dict):
            comp = value.get("pct") or value.get("num") or value.get("count")
            value = value.get("answer", comp is not None)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            comp, value = value, value > 0
        if value is True:
            self.check(f"{base}_yes")
            if companion_suffix and comp not in (None, ""):
                self.set(f"{base}_{companion_suffix}", money(comp) if companion_suffix != "pct" else str(comp))
        elif value is False:
            self.check(f"{base}_no")

    def choice(self, spec: dict, value, path: str):
        if value in (None, ""):
            return
        alias = spec.get("alias", {})
        v = str(value).strip().lower() if not isinstance(value, (int, float)) else value
        if isinstance(v, str):
            v = alias.get(v, v)
        if isinstance(v, str) and v in spec["opts"]:
            self.check(f"{spec['base']}_{v}")
            return
        if "other" in spec:
            self.check(f"{spec['base']}_other")
            suffix = "other_amt" if spec["other"] == "amt" else "other_desc"
            self.set(f"{spec['base']}_{suffix}", money(value) if spec["other"] == "amt" else str(value))
        else:
            self.warnings.append(f"{path}: '{value}' is not one of {spec['opts']} - skipped")

    # -- sections --
    def run(self):
        d = self.data
        for path, field in TEXT_MAP.items():
            v = get_path(d, path)
            if v in (None, ""):
                continue
            if field in ("p1_expiring_premium", "p4_1_year_estimated_gross_revenue",
                         "p8_maximum_value_per_vehicle", "p8_average_value_per_vehicle",
                         "veh_total_value"):
                v = money(v)
            elif field in PHONE_FIELDS:
                v = formatting.format_phone(v)
            elif field in DATE_FIELDS:
                v = formatting.format_date(v)
            elif field in DASH_STRIP_FIELDS:
                v = formatting.strip_cosmetic_dashes(v)
            if v in (None, ""):
                continue          # normalisation could not make sense of it - blank beats wrong
            self.set(field, str(v))

        for path, (base, comp) in YESNO_MAP.items():
            self.yesno(base, get_path(d, path), comp)

        for section, mapping in PCT_SECTIONS.items():
            block = d.get(section) or {}
            if not isinstance(block, dict):
                self.warnings.append(f"{section}: expected an object, got {type(block).__name__}")
                continue
            for key, value in block.items():
                if key in mapping:
                    self.yesno(mapping[key], value, "pct")
                else:
                    self.warnings.append(f"{section}.{key}: unknown key - skipped")

        for path, spec in CHOICE_MAP.items():
            self.choice(spec, get_path(d, path), path)

        # workers comp: none | no_policy | active(expires)
        wc = get_path(d, "coverages.workers_comp")
        if isinstance(wc, dict):
            status, exp = wc.get("status"), wc.get("expires")
            if status == "none":
                self.check("p2_workers_compensation_none")
            elif status == "no_policy":
                self.check("p2_workers_compensation_no_policy")
            if exp:
                self.check("p2_workers_compensation_expires_chk")
                self.set("p2_workers_compensation_expires", str(exp))
        elif isinstance(wc, str) and wc:
            if wc in ("none", "no_policy"):
                self.check(f"p2_workers_compensation_{wc}")
            else:
                self.check("p2_workers_compensation_expires_chk")
                self.set("p2_workers_compensation_expires", wc)

        for path, (base, comp_suffix) in TRIPLE_MAP.items():
            v = get_path(d, path)
            if v is None:
                continue
            if v is False:
                self.check(f"{base}_no")
            elif isinstance(v, str) and v.strip().lower() in ("na", "n/a"):
                self.check(f"{base}_na")
            elif v:
                self.check(f"{base}_yes")
                if isinstance(v, str):
                    self.set(f"{base}_{comp_suffix}", v)
                elif isinstance(v, dict) and v.get(comp_suffix):
                    self.set(f"{base}_{comp_suffix}", str(v[comp_suffix]))

        for path, (base, allowed) in MULTI_MAP.items():
            v = get_path(d, path)
            if v in (None, ""):
                continue
            items = v if isinstance(v, list) else [v]
            for item in items:
                key = str(item).strip().lower().replace(" ", "_")
                if key in allowed:
                    self.check(f"{base}_{key}")
                else:
                    self.warnings.append(f"{path}: '{item}' not in {sorted(allowed)} - skipped")

        self.vehicles(d.get("vehicles") or [])
        self.drivers(d.get("drivers") or [])
        self.loss_runs(d.get("loss_runs") or [])
        self.contracts(d.get("contracts") or [])

        # raw field escape hatch - wins over everything semantic
        for field, value in (d.get("fields") or {}).items():
            if isinstance(value, bool):
                self.values[field] = value
            else:
                self.set(field, str(value))

    def vehicles(self, vehicles: list):
        if len(vehicles) > MAX_VEHICLES:
            self.warnings.append(
                f"{len(vehicles)} vehicles provided; the form holds {MAX_VEHICLES}. "
                f"Vehicles {MAX_VEHICLES + 1}+ were NOT placed - needs an overflow page."
            )
        for i, v in enumerate(vehicles[:MAX_VEHICLES], start=1):
            n = f"{i:02d}"
            desc = join_parts([
                join_parts([v.get("year"), v.get("maker"), v.get("model")], " "),
                v.get("body_type"),
                f"GVW {money(v['gvw'])}" if v.get("gvw") else None,
                f"tows {v['max_towed']}" if v.get("max_towed") is not None else None,
            ])
            self.set(f"p5_veh{n}_desc", desc)

            stated, perm = v.get("stated_value"), v.get("perm_equip_value")
            parts = []
            if stated is not None:
                parts.append(money(stated))
            if perm is not None:
                parts.append(f"+ ${money(perm)}")
                try:
                    total = float(str(stated).replace(",", "").replace("$", "")) + \
                        float(str(perm).replace(",", "").replace("$", ""))
                    parts.append(f"= ${money(total)}")
                except (TypeError, ValueError):
                    pass
            if v.get("vin"):
                parts.append(f"VIN {v['vin']}")
            self.set(f"p5_veh{n}_value_vin", join_parts(parts, "  "))

            self.set(f"p6_veh{n}_yearmaker",
                     join_parts([v.get("year"), v.get("maker")], " "))
            onhook = v.get("onhook")
            if onhook not in (None, ""):
                key = str(onhook).strip().lower()
                if key in ("none", "100k", "50k", "25k"):
                    self.check(f"p6_veh{n}_onhook_{key}")
                else:
                    self.check(f"p6_veh{n}_onhook_other")
                    self.set(f"p6_veh{n}_onhook_other_amt", money(onhook))

    def drivers(self, drivers: list):
        if len(drivers) > MAX_DRIVERS:
            self.warnings.append(
                f"{len(drivers)} drivers provided; the form holds {MAX_DRIVERS}. "
                f"Drivers {MAX_DRIVERS + 1}+ were NOT placed - needs an overflow page."
            )
        for i, drv in enumerate(drivers[:MAX_DRIVERS], start=1):
            n = f"{i:02d}"
            self.set(f"p7_drv{n}_name", drv.get("name"))
            # date_of_hire can be MM/YYYY (no day) - format_date can't parse
            # that shape and isn't asked to; only birthday is a full date.
            details = join_parts([
                drv.get("state"),
                formatting.strip_cosmetic_dashes(drv.get("license")),
                formatting.format_date(drv.get("birthday")),
                drv.get("position"),
                f"{drv['years_experience']} yrs" if drv.get("years_experience") is not None else None,
                f"hired {drv['date_of_hire']}" if drv.get("date_of_hire") else None,
            ])
            self.set(f"p7_drv{n}_details", details)

    def loss_runs(self, runs: list):
        try:
            runs = sorted(runs, key=lambda r: int(r.get("year", 0)), reverse=True)
        except (TypeError, ValueError):
            pass
        if len(runs) > len(LR_BLOCKS):
            self.warnings.append(f"{len(runs)} loss-run years provided; form has {len(LR_BLOCKS)} blocks.")
        for block, printed_year, run in zip(LR_BLOCKS, LR_PRINTED_YEARS, runs):
            year = run.get("year")
            if year and int(year) != printed_year:
                self.notes.append(
                    f"loss runs: data year {year} placed in the block printed '{printed_year}' "
                    f"(the source form's year labels are off - flagged to fix with JC)."
                )
            if run.get("no_policy"):
                self.check(f"p9_{block}_no_policy")
                continue
            self.set(f"p9_{block}_carrier", run.get("carrier"))
            self.set(f"p9_{block}_policy_number", run.get("policy_number"))
            self.set(f"p9_{block}_effective_dates",
                     _period(run.get("effective_dates")))
            if run.get("premium") is not None:
                self.set(f"p9_{block}_premium", money(run["premium"]))
            broker = str(run.get("broker", "")).strip().lower()
            if broker == "sierra":
                self.check(f"p9_{block}_broker_sierra")
            elif broker:
                self.check(f"p9_{block}_broker_other_broker")

    def contracts(self, contracts: list):
        if len(contracts) > MAX_CONTRACTS:
            self.warnings.append(f"{len(contracts)} contracts provided; form holds {MAX_CONTRACTS}.")
        for i, c in enumerate(contracts[:MAX_CONTRACTS], start=1):
            self.set(f"p2_contract{i}_name", c.get("name"))
            if c.get("pct") is not None:
                self.set(f"p2_contract{i}_pct", str(c["pct"]))


# ---------------------------------------------------------------- main


def apply_defaults(data: dict) -> tuple[dict, list[str]]:
    """Merge defaults UNDER data (client data always wins). Returns applied list."""
    if not os.path.exists(DEFAULTS):
        return data, []
    defaults = json.load(open(DEFAULTS, encoding="utf-8"))
    applied = []
    for path, value in defaults.items():
        if get_path(data, path) in (None, ""):
            cur = data
            parts = path.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
            applied.append(f"{path} = {value!r}")
    return data, applied


# JC's formatting spec (call 7.22.26): max 10pt, dark blue instead of black.
# Final signature copies flatten to Century Gothic at the finalize step.
TEXT_COLOR = (0.10, 0.15, 0.40)
MAX_FONT_SIZE = 10.0
MIN_FONT_SIZE = 5.5


def fitting_fontsize(text: str, rect: fitz.Rect, multiline: bool) -> float:
    if multiline:
        return 8.0
    avail = max(rect.width - 4, 8)
    needed = fitz.get_text_length(text, fontname="helv", fontsize=MAX_FONT_SIZE)
    if needed <= avail:
        return MAX_FONT_SIZE
    return max(MIN_FONT_SIZE, MAX_FONT_SIZE * avail / needed)


def fill_pdf(values: dict) -> tuple[fitz.Document, int, int]:
    doc = fitz.open(TEMPLATE)
    n_text = n_box = 0
    known = set()
    for page in doc:
        for w in page.widgets():
            known.add(w.field_name)
            if w.field_name not in values:
                continue
            v = values[w.field_name]
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                w.field_value = bool(v)
                n_box += 1
            else:
                text = str(v)
                multiline = bool(w.field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE)
                w.text_fontsize = fitting_fontsize(text, w.rect, multiline)
                w.text_color = TEXT_COLOR
                w.field_value = text
                n_text += 1
            w.update()
    unknown = [k for k in values if k not in known]
    return doc, n_text, n_box, unknown


def consistency_flags(d: dict) -> list[str]:
    """Mechanical cross-checks that run on every fill - no judgment involved."""
    def num(v):
        try:
            return float(str(v).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            return None

    flags = []
    vehicles = d.get("vehicles") or []
    drivers = d.get("drivers") or []

    # Legal name against the domain the insured gave. Brookfield Towing, LLC
    # runs capitAlvalleytowing.com — one letter apart, on JC's own real file, and
    # it went through unremarked. It is usually nothing (a domain bought with a
    # typo years ago) and occasionally the thread that unravels a file, so it is
    # raised as a question and never acted on.
    site = str(get_path(d, "company.website") or "")
    name = str(get_path(d, "company.first_named_insured")
               or get_path(d, "company.dba") or "")
    if site and name:
        host = re.sub(r"^https?://", "", site).split("/")[0].lower()
        host = re.sub(r"^www\.", "", host)
        stem = re.sub(r"\.(com|net|org|co|us|biz|info)$", "", host)
        stem_letters = re.sub(r"[^a-z]", "", stem)
        # drop the words a domain never carries, then compare what is left
        name_letters = re.sub(r"[^a-z]", "", re.sub(
            r"\b(llc|inc|incorporated|corp|corporation|co|ltd|the|and|dba)\b", " ",
            name.lower()))
        # A domain that is a truncation of the name is the normal case, not a
        # finding: `ridgelinetow.com` for Ridgeline Towing & Recovery is how every
        # business shortens itself. What matters is letters that DIVERGE —
        # capitAl against capitOl splits at the fifth character. Flagging honest
        # abbreviations would teach the team to ignore these flags, which costs
        # more than the flag is worth.
        abbreviation = (name_letters.startswith(stem_letters)
                        or stem_letters.startswith(name_letters))
        if stem_letters and name_letters and not abbreviation:
            import difflib
            ratio = difflib.SequenceMatcher(None, stem_letters, name_letters).ratio()
            if ratio >= 0.75:
                flags.append(
                    f"Named insured “{name}” and website domain “{host}” differ by a "
                    f"few letters — confirm the domain belongs to this entity and is "
                    f"not a lookalike.")
            else:
                flags.append(
                    f"Website “{host}” does not resemble the named insured "
                    f"“{name}” — confirm it is the insured's own site.")

    tv = num(get_path(d, "company.total_vehicles"))
    if tv is not None and vehicles and tv != len(vehicles):
        flags.append(f"client declares {int(tv)} vehicles but {len(vehicles)} are listed - where are the rest?")
    td = num(get_path(d, "company.total_drivers"))
    if td is not None and drivers and td != len(drivers):
        flags.append(f"client declares {int(td)} drivers but {len(drivers)} are listed")

    tsv = num(get_path(d, "vehicle_totals.stated_value"))
    if tsv is not None and vehicles:
        total = sum((num(v.get("stated_value")) or 0) + (num(v.get("perm_equip_value")) or 0)
                    for v in vehicles)
        if total and abs(total - tsv) > 1:
            flags.append(f"declared total stated value {tsv:,.0f} but the vehicle list sums to {total:,.0f}")

    pu = num(get_path(d, "vehicle_totals.power_units"))
    tr = num(get_path(d, "vehicle_totals.trailers"))
    if None not in (pu, tr, tv) and pu + tr != tv:
        flags.append(f"power units ({pu:.0f}) + trailers ({tr:.0f}) do not add up to total vehicles ({tv:.0f})")

    for section in ("revenue_sources", "operations", "goods_hauled", "radius"):
        block = d.get(section) or {}
        pcts = []
        for v in block.values():
            if isinstance(v, dict) and v.get("pct") is not None:
                pcts.append(num(v["pct"]) or 0)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                pcts.append(float(v))
        pcts = [p for p in pcts if p]
        if pcts and abs(sum(pcts) - 100) > 0.5:
            flags.append(f"{section} percentages sum to {sum(pcts):.0f}% (form requires 100%)")

    years = [r.get("year") for r in (d.get("loss_runs") or []) if r.get("year")]
    if len(years) != len(set(years)):
        flags.append("duplicate years in the loss runs list")
    return flags


# Yes/no rows the form asks and a submission cannot leave open. These are not
# "missing data" in the sense of something nobody has collected — they are
# questions the client skipped, and the difference matters: a blank here is a
# phone call, not a research task. Ridgeline arrived with `Cross state lines`
# unticked and the engine correctly refused to guess, then said nothing about it.
MUST_ANSWER = {
    "company.cross_state_lines": "Cross state lines — yes or no?",
    "company.home_based": "Home based business — yes or no?",
    "company.new_venture": "New venture — yes or no?",
    "company.dash_cameras": "Vehicle dash cameras — installed or not?",
    "company.telematics": "Vehicle telematics — installed or not?",
    "company.entity_type": "Company type — sole proprietor, corporation or LLC?",
}


def unanswered_questions(data: dict) -> list[str]:
    """Form questions left blank, phrased as what to ask the insured."""
    out = []
    for path, question in MUST_ANSWER.items():
        if get_path(data, path) is None:
            out.append(question)
    # An MC number is federal interstate authority; leaving the interstate
    # question blank next to one is the kind of contradiction an underwriter
    # spots immediately, so name it rather than just listing the blank.
    if (get_path(data, "company.cross_state_lines") is None
            and get_path(data, "company.mc_number")):
        out.append(f"Cross state lines is blank but the app carries MC number "
                   f"{get_path(data, 'company.mc_number')} — interstate authority "
                   f"implies yes; confirm with the insured.")
    return out


def derive_location(data: dict) -> None:
    """Seed the Location schedule from page 1's company answers.

    The form asks for the operating address twice: once in Company info on page 1
    and again as the first row of the Location schedule on page 8. It is one
    datum, so it is extracted once — into `company` — and copied here rather than
    asking the reader for it twice and risking two different answers. Without
    this, page 8 shipped with 1 of 53 fields filled while the address sat in the
    dossier all along.
    """
    c = data.get("company") or {}
    loc = data.setdefault("location", {}) if isinstance(data.get("location", {}), dict) \
        else data.setdefault("location", {})
    # Clients write "same" rather than repeat the address they just gave. The
    # word is an instruction, not an address, and it must not reach the Location
    # schedule — an underwriter reading "same" on page 8 has no idea what it
    # refers to once the packet is split up.
    here = str(c.get("location_address") or "").strip().lower().rstrip(".")
    if here in ("same", "same as above", "as above", "same as mailing",
                "same as mailing address", "idem", "same address"):
        c["location_address"] = c.get("mailing_address") or c.get("location_address")
    if not loc.get("address"):
        addr = c.get("location_address") or c.get("mailing_address")
        if addr and str(addr).strip().lower().rstrip(".") not in ("same", "as above"):
            loc["address"] = addr
    if loc.get("home_based") is None and c.get("home_based") is not None:
        loc["home_based"] = c.get("home_based")
    if not loc.get("commercial_location_number"):
        n = c.get("total_commercial_locations")
        # "0 commercial locations" means the yard is the home address; it is still
        # location 1 on the schedule, because the schedule numbers places, not
        # leases.
        loc["commercial_location_number"] = 1 if str(n or "0") in ("0", "1") else n




def _period(value) -> str:
    """A policy period in JC's date shape — both ends of it.

    `format_date` already understands a range; these two writers simply never
    called it, so periods reached page 9 and page 10 exactly as the carrier's
    PDF printed them ("08/03/2023 - 08/03/2024"). Anything it cannot parse is
    kept verbatim rather than blanked: a broker's note about a term is the only
    record of that year, and losing it is worse than an unformatted string.
    """
    if value in (None, ""):
        return ""
    return formatting.format_date(value) or str(value)


def lossrun_page_values(data: dict) -> dict:
    """Field values for the Loss runs Request form (p10) and Scores (p11).

    The rows carry printed year labels from whenever the form was authored, but
    they are POSITIONAL — JC's real QP has "2025" written into the row printed
    "2024". Newest term goes in row 1, and so on down. Data comes from the
    loss_runs section the gospel pass maintains.
    """
    runs = [r for r in (data.get("loss_runs") or [])
            if isinstance(r, dict) and r.get("year")]
    runs.sort(key=lambda r: str(r.get("year")), reverse=True)
    out: dict = {}
    units = None
    try:
        units = int(str(get_path(data, "vehicle_totals.power_units")
                        or get_path(data, "company.total_vehicles") or "") or 0)
    except ValueError:
        units = None
    for i, r in enumerate(runs[:5], 1):
        sfx = "" if i == 1 else f"_{i}"
        # request form (p10): row 1 is the newest year the form asks for
        out[f"p10_{2025 - i}_insurance_carrier"] = r.get("carrier") or ""
        out[f"p10_policy_number{sfx}"] = r.get("policy_number") or ""
        out[f"p10_effective_dates{sfx}"] = _period(r.get("effective_dates"))
        # scores (p11)
        out[f"p11_{2026 - i}_insurance_carrier"] = r.get("carrier") or ""
        n = r.get("claim_count")
        if n is not None:
            out[f"p11_total_number_of_claims_paid{sfx}"] = str(n)
        t = r.get("total_incurred")
        if t is not None:
            out[f"p11_total_claims_paid{sfx}"] = f"{float(t):,.0f}"
        prem = r.get("annual_premium")
        if prem:
            out[f"p10_annual_premium{sfx}"] = str(prem)
            out[f"p11_annual_premium{sfx}"] = str(prem)
            if units:
                try:
                    per = float(str(prem).replace(",", "")) / units
                    out[f"p11_average_price_per_power_unit{sfx}"] = f"{per:,.0f}"
                except ValueError:
                    pass
    # drop empties so blanks stay blank instead of writing ""
    return {k: v for k, v in out.items() if v not in ("", None)}


def missing_key_fields(data: dict) -> list[str]:
    raw = data.get("fields") or {}
    out = []
    for path in KEY_FIELDS:
        v = get_path(data, path)
        if v not in (None, "", [], {}):
            continue
        if any(raw.get(f) not in (None, "", False) for f in RAW_KEY_EQUIV.get(path, [])):
            continue
        out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True, help="client / SP name")
    ap.add_argument("--data", help="new data JSON to merge into the client state")
    ap.add_argument("--no-defaults", action="store_true", help="skip cookie-cutter defaults")
    ap.add_argument("--open", action="store_true", help="open the filled PDF when done")
    ap.add_argument("--new", action="store_true",
                    help="live call mode: open a blank draft PDF to type into")
    ap.add_argument("--sync", action="store_true",
                    help="pull values typed into the draft PDF back into the state + report gaps")
    args = ap.parse_args()

    slug = slugify(args.client)
    cdir = os.path.join(CLIENTS, slug)
    os.makedirs(cdir, exist_ok=True)
    state_path = os.path.join(cdir, "state.json")
    out_pdf = os.path.join(cdir, f"{slug}_CAP_app_2026_DRAFT.pdf")

    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}

    if args.new:
        import shutil
        if not os.path.exists(out_pdf):
            shutil.copyfile(TEMPLATE, out_pdf)
        if not os.path.exists(state_path):
            json.dump({}, open(state_path, "w", encoding="utf-8"))
        os.startfile(out_pdf)
        print(f"Draft open for live entry: {out_pdf}")
        print("Type into the form during the call, save, then run --sync for the gap report.")
        return 0

    if args.sync:
        if not os.path.exists(out_pdf):
            print("No draft PDF for this client yet - run --new first.", file=sys.stderr)
            return 2
        from extract_data import extract
        typed = extract(out_pdf)["fields"]
        state = deep_merge(state, {"fields": typed})
        json.dump(state, open(state_path, "w", encoding="utf-8"), indent=1)
        missing = missing_key_fields(state)
        print(f"# Sync report - {args.client}")
        print(f"- Captured from draft: {len(typed)} fields")
        print("\n## Missing key fields")
        print("\n".join(f"- {m}" for m in missing) or "- none - ready for broker review")
        return 0
    if args.data:
        new_data = json.load(open(args.data, encoding="utf-8"))
        state = deep_merge(state, new_data)
    if not state:
        print("No data: state is empty and no --data given.", file=sys.stderr)
        return 2
    json.dump(state, open(state_path, "w", encoding="utf-8"), indent=1)

    working = deepcopy(state)
    derive_location(working)
    applied = []
    if not args.no_defaults:
        working, applied = apply_defaults(working)

    filler = Filler(working)
    filler.run()
    # loss run pages are positional rows fed by the gospel pass
    for k, v in lossrun_page_values(working).items():
        filler.values.setdefault(k, v)
    doc, n_text, n_box, unknown = fill_pdf(filler.values)
    out_pdf = os.path.join(cdir, f"{slug}_CAP_app_2026_DRAFT.pdf")
    doc.save(out_pdf)

    missing = missing_key_fields(working)
    unanswered = unanswered_questions(working)
    red_flags = consistency_flags(working)
    if unknown:
        filler.warnings.append(f"unknown raw field names (typos?): {unknown}")

    report = [
        f"# CAP app fill report - {args.client}",
        "",
        f"- Output: {out_pdf}",
        f"- Filled: {n_text} text fields, {n_box} checkboxes",
        f"- Vehicles: {len(working.get('vehicles') or [])} | Drivers: {len(working.get('drivers') or [])}"
        f" | Loss-run years: {len(working.get('loss_runs') or [])}",
        "",
        "## Consistency red flags (auto-checked)",
        *([f"- {f}" for f in red_flags] or ["- none"]),
        "",
        "## Applied defaults (confirm with client)",
        *([f"- {a}" for a in applied] or ["- none"]),
        "",
        "## Missing key fields",
        *([f"- {m}" for m in missing] or ["- none - ready for broker review"]),
        "",
        "## Questions the client left blank (ask them)",
        *([f"- {q}" for q in unanswered] or ["- none"]),
        "",
        "## Warnings",
        *([f"- {w}" for w in filler.warnings] or ["- none"]),
        "",
        "## Notes",
        *([f"- {n}" for n in filler.notes] or ["- none"]),
    ]
    report_path = os.path.join(cdir, "report.md")
    open(report_path, "w", encoding="utf-8").write("\n".join(report))
    print("\n".join(report))

    if args.open:
        os.startfile(out_pdf)  # noqa: S606 - intentional, opens in default viewer
    return 0


if __name__ == "__main__":
    sys.exit(main())
