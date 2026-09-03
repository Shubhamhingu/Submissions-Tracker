"""CSV import for submissions.csv.

Incremental by design: rows are matched to existing opportunities by a dedupe
key built from ``submissionDate | vendor | position_title | endClient`` (all
whitespace-normalised and lower-cased).  Existing rows have only their
CSV-sourced columns refreshed -- the fields you fill in by hand (employment
type, pay, work mode, onsite location) and all interview data are left alone.
"""
from __future__ import annotations

import csv
import io
import re

from dateutil import parser as dateparser

from models import (
    Opportunity,
    clean,
    clean_keep_sentinel,
    db,
    get_or_create,
)
from models import (
    Company,
    EndClient,
    ImpPartner,
    MarketingProfile,
    Pod,
    PrimeVendor,
    Recruiter,
    Technology,
    Vendor,
    Vrm,
)

# CSV header -> (dimension key, model)
_DIM_COLUMNS = {
    "ps_name": ("recruiter", Recruiter),
    "marketingName": ("marketing_profile", MarketingProfile),
    "company": ("company", Company),
    "technology": ("technology", Technology),
    "endClient": ("end_client", EndClient),
    "vendor": ("vendor", Vendor),
    "impPartner": ("imp_partner", ImpPartner),
    "primeVendor": ("prime_vendor", PrimeVendor),
    "podName": ("pod", Pod),
    "vrmName": ("vrm", Vrm),
}


def _parse_date(value):
    value = clean(value)
    if not value:
        return None
    try:
        return dateparser.parse(value, dayfirst=False).date()
    except (ValueError, OverflowError):
        return None


def _norm(value):
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def make_dedupe_key(row):
    return "|".join(
        _norm(row.get(col, ""))
        for col in ("submissionDate", "vendor", "position_title", "endClient")
    )


def import_csv(text: str) -> dict:
    """Import CSV *text*.  Returns a summary dict: created / updated / skipped."""
    reader = csv.DictReader(io.StringIO(text))
    created = updated = skipped = 0

    for row in reader:
        key = make_dedupe_key(row)
        if not key.strip("|"):
            skipped += 1
            continue

        opp = db.session.execute(
            db.select(Opportunity).filter_by(dedupe_key=key)
        ).scalar_one_or_none()
        is_new = opp is None
        if is_new:
            opp = Opportunity(dedupe_key=key, source="csv")
            db.session.add(opp)

        # --- CSV-sourced fields (always refreshed) ------------------------
        for col, (attr, model) in _DIM_COLUMNS.items():
            setattr(opp, f"{attr}_id", _dim_id(model, row.get(col)))

        opp.status = clean(row.get("status"))
        opp.submission_date = _parse_date(row.get("submissionDate"))
        opp.confirmation_date = _parse_date(row.get("confirmationdate"))
        opp.actual_start = _parse_date(row.get("actual_start"))
        opp.position_title = clean(row.get("position_title"))
        opp.duration = clean(row.get("duration"))
        opp.csv_is_direct = clean_keep_sentinel(row.get("isdirect"))
        opp.csv_is_remote = clean_keep_sentinel(row.get("isremote"))

        # Job description is maintained by hand in the app.  Only take it from
        # the CSV when the CSV actually carries one and we don't already have a
        # value -- never blank out something the user typed.
        csv_jd = clean(row.get("jobdescription"))
        if csv_jd and not opp.job_description:
            opp.job_description = csv_jd

        # --- manual defaults: only seed work_mode on brand-new rows -------
        if is_new:
            opp.work_mode = _default_work_mode(row.get("isremote"))
            created += 1
        else:
            updated += 1

    db.session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def _dim_id(model, raw):
    obj = get_or_create(model, raw)
    return obj.id if obj else None


def _default_work_mode(raw):
    v = _norm(raw)
    if "remote" in v:
        return "Remote"
    if "hybrid" in v:
        return "Hybrid"
    if "on" in v and "site" in v:
        return "Onsite"
    return None


def import_csv_path(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return import_csv(fh.read())
