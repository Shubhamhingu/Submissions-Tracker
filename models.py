"""Normalized SQLite schema for the job-submission / interview tracker.

Design
------
* Lookup ("dimension") tables hold each distinct organisation / person name once:
  recruiter, marketing profile, company, technology, end client, vendor,
  implementation partner, prime vendor, pod, VRM.
* ``Opportunity`` is one job you were submitted to.  It carries foreign keys to
  the dimension tables plus the fields you fill in by hand (employment type,
  pay, work mode, onsite location).
* ``InterviewRound`` is one round for an opportunity (round 1, 2, 3, ...).  It
  holds the schedule, the post-interview status, the SME (technical) review and
  the PS (pod / recruiter) review, an outcome and an "is final round" flag.
* ``RescheduleHistory`` keeps the previous date/time whenever a round is moved,
  so the round number stays stable.

Every incoming text value is passed through ``clean()`` which trims surrounding
whitespace and collapses internal runs of whitespace.  The sentinel values
``"-"``, ``"TBD"``, ``"N/A"`` and ``""`` are treated as "no value" (NULL).
"""
from __future__ import annotations

import re
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

_EMPTY_SENTINELS = {"", "-", "--", "n/a", "na", "tbd", "none", "null"}


def clean(value):
    """Trim + collapse whitespace.  Return None for empty/sentinel values."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in _EMPTY_SENTINELS:
        return None
    return text or None


def clean_keep_sentinel(value):
    """Like :func:`clean` but keeps the text even if it is a sentinel."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


# --------------------------------------------------------------------------- #
# Dimension tables
# --------------------------------------------------------------------------- #
class _NamedMixin:
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<{self.__class__.__name__} {self.name!r}>"


class Recruiter(_NamedMixin, db.Model):
    __tablename__ = "recruiter"


class MarketingProfile(_NamedMixin, db.Model):
    __tablename__ = "marketing_profile"


class Company(_NamedMixin, db.Model):
    __tablename__ = "company"


class Technology(_NamedMixin, db.Model):
    __tablename__ = "technology"


class EndClient(_NamedMixin, db.Model):
    __tablename__ = "end_client"


class Vendor(_NamedMixin, db.Model):
    __tablename__ = "vendor"


class ImpPartner(_NamedMixin, db.Model):
    __tablename__ = "imp_partner"


class PrimeVendor(_NamedMixin, db.Model):
    __tablename__ = "prime_vendor"


class Pod(_NamedMixin, db.Model):
    __tablename__ = "pod"


class Vrm(_NamedMixin, db.Model):
    __tablename__ = "vrm"


_DIMENSIONS = {
    "recruiter": Recruiter,
    "marketing_profile": MarketingProfile,
    "company": Company,
    "technology": Technology,
    "end_client": EndClient,
    "vendor": Vendor,
    "imp_partner": ImpPartner,
    "prime_vendor": PrimeVendor,
    "pod": Pod,
    "vrm": Vrm,
}


def get_or_create(model, name):
    """Return the row for ``name`` in a dimension table, creating it if needed.

    ``name`` is cleaned first; an empty value yields ``None``.
    """
    name = clean(name)
    if not name:
        return None
    row = db.session.execute(
        db.select(model).filter_by(name=name)
    ).scalar_one_or_none()
    if row is None:
        row = model(name=name)
        db.session.add(row)
        db.session.flush()
    return row


# --------------------------------------------------------------------------- #
# Choice constants
# --------------------------------------------------------------------------- #
EMPLOYMENT_TYPES = ["Full-time", "C2C", "W2", "1099"]
PAY_RATE_TYPES = ["Yearly", "Hourly"]
WORK_MODES = ["Remote", "Onsite", "Hybrid"]
ROUND_STATUSES = ["Scheduled", "Completed", "Rescheduled", "Cancelled", "No-show"]
ROUND_OUTCOMES = ["Pending", "Pass", "Fail"]
INTERVIEW_MODES = ["Video", "Phone", "Onsite", "Take-home"]


# --------------------------------------------------------------------------- #
# Fact tables
# --------------------------------------------------------------------------- #
class Opportunity(db.Model):
    __tablename__ = "opportunity"

    id = db.Column(db.Integer, primary_key=True)

    # ---- from the CSV -----------------------------------------------------
    recruiter_id = db.Column(db.ForeignKey("recruiter.id"))
    marketing_profile_id = db.Column(db.ForeignKey("marketing_profile.id"))
    company_id = db.Column(db.ForeignKey("company.id"))
    technology_id = db.Column(db.ForeignKey("technology.id"))
    end_client_id = db.Column(db.ForeignKey("end_client.id"))
    vendor_id = db.Column(db.ForeignKey("vendor.id"))
    imp_partner_id = db.Column(db.ForeignKey("imp_partner.id"))
    prime_vendor_id = db.Column(db.ForeignKey("prime_vendor.id"))
    pod_id = db.Column(db.ForeignKey("pod.id"))
    vrm_id = db.Column(db.ForeignKey("vrm.id"))

    status = db.Column(db.String(50))
    submission_date = db.Column(db.Date, index=True)
    confirmation_date = db.Column(db.Date)
    actual_start = db.Column(db.Date)
    position_title = db.Column(db.String(500))
    duration = db.Column(db.String(100))
    job_description = db.Column(db.Text)
    csv_is_direct = db.Column(db.String(50))
    csv_is_remote = db.Column(db.String(50))

    # Stable key for incremental CSV re-import (dedupe).
    dedupe_key = db.Column(db.String(700), unique=True, index=True)
    source = db.Column(db.String(20), default="csv")  # csv | manual

    # ---- filled in by hand ---------------------------------------------- #
    employment_type = db.Column(db.String(30))
    pay_rate_type = db.Column(db.String(20))
    pay_min = db.Column(db.Float)
    pay_max = db.Column(db.Float)
    pay_currency = db.Column(db.String(10), default="USD")
    comp_notes = db.Column(db.Text)
    work_mode = db.Column(db.String(20))
    onsite_city = db.Column(db.String(120))
    onsite_state = db.Column(db.String(120))

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    recruiter = db.relationship("Recruiter")
    marketing_profile = db.relationship("MarketingProfile")
    company = db.relationship("Company")
    technology = db.relationship("Technology")
    end_client = db.relationship("EndClient")
    vendor = db.relationship("Vendor")
    imp_partner = db.relationship("ImpPartner")
    prime_vendor = db.relationship("PrimeVendor")
    pod = db.relationship("Pod")
    vrm = db.relationship("Vrm")
    rounds = db.relationship(
        "InterviewRound",
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="InterviewRound.round_number, InterviewRound.scheduled_date, InterviewRound.id",
    )

    # -- convenience ----------------------------------------------------- #
    def name(self, model_attr):
        obj = getattr(self, model_attr)
        return obj.name if obj else None

    @property
    def pay_display(self):
        if self.pay_min is None and self.pay_max is None:
            return None
        cur = self.pay_currency or "USD"
        unit = "/yr" if self.pay_rate_type == "Yearly" else ("/hr" if self.pay_rate_type == "Hourly" else "")

        def fmt(v):
            if v is None:
                return None
            return f"{v:,.0f}" if v >= 1000 else f"{v:g}"

        lo, hi = fmt(self.pay_min), fmt(self.pay_max)
        if lo and hi and lo != hi:
            return f"{cur} {lo}-{hi}{unit}"
        return f"{cur} {lo or hi}{unit}"

    @property
    def max_round(self):
        return max((r.round_number for r in self.rounds), default=0)

    @property
    def round_numbers(self):
        """Distinct round numbers reached (rescheduled/repeat entries collapsed)."""
        return sorted({r.round_number for r in self.rounds})

    @property
    def has_final(self):
        return any(r.is_final for r in self.rounds)

    def rounds_for(self, number):
        return [r for r in self.rounds if r.round_number == number]


class InterviewRound(db.Model):
    __tablename__ = "interview_round"

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(db.ForeignKey("opportunity.id"), nullable=False, index=True)
    round_number = db.Column(db.Integer, nullable=False, default=1)

    scheduled_date = db.Column(db.Date, index=True)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    interview_mode = db.Column(db.String(20))

    # An opportunity may hold several rows for the same round_number -- e.g. a
    # round that was rescheduled/cancelled kept as its own record alongside the
    # one that actually took place.  Dashboard "rounds reached" counts distinct
    # round numbers so these do not double-count.
    status = db.Column(db.String(20), default="Scheduled")
    outcome = db.Column(db.String(20), default="Pending")
    sme_review = db.Column(db.Text)   # technical / subject-matter feedback
    ps_review = db.Column(db.Text)    # pod / recruiter (presales) feedback
    is_final = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    opportunity = db.relationship("Opportunity", back_populates="rounds")
    reschedules = db.relationship(
        "RescheduleHistory",
        back_populates="interview_round",
        cascade="all, delete-orphan",
        order_by="RescheduleHistory.changed_at",
    )


class RescheduleHistory(db.Model):
    __tablename__ = "reschedule_history"

    id = db.Column(db.Integer, primary_key=True)
    interview_round_id = db.Column(db.ForeignKey("interview_round.id"), nullable=False)

    old_date = db.Column(db.Date)
    old_start_time = db.Column(db.Time)
    old_end_time = db.Column(db.Time)
    new_date = db.Column(db.Date)
    new_start_time = db.Column(db.Time)
    new_end_time = db.Column(db.Time)
    reason = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.now)

    interview_round = db.relationship("InterviewRound", back_populates="reschedules")
