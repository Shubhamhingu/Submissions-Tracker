"""Count Automation -- job submission & interview tracker (Flask).

Run locally:
    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000  (default password: "changeme")

Deploy on PythonAnywhere: point the WSGI file at ``wsgi.py`` (see that file).
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash

from config import Config
from importer import import_csv, import_csv_path
from models import (
    EMPLOYMENT_TYPES,
    INTERVIEW_MODES,
    PAY_RATE_TYPES,
    ROUND_OUTCOMES,
    ROUND_STATUSES,
    WORK_MODES,
    InterviewRound,
    Opportunity,
    RescheduleHistory,
    clean,
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


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    db.init_app(app)

    with app.app_context():
        _bootstrap_db(app)
        _run_migrations()
        db.create_all()

    register_routes(app)
    register_jinja(app)
    return app


def _bootstrap_db(app):
    """On a fresh SQLite file, load seed.sql if it's present.

    Lets a new deploy (e.g. Render, whose filesystem starts empty) come up with
    the data committed in the repo instead of an empty database.  A no-op once
    the database file exists.  Never fatal: if anything goes wrong the app still
    starts and ``db.create_all()`` builds empty tables.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        return
    path = uri[len("sqlite:///"):]
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    seed = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.sql")
    if not os.path.exists(seed):
        return
    import sqlite3

    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            with open(seed, "r", encoding="utf-8") as fh:
                conn.executescript(fh.read())
            conn.commit()
        finally:
            conn.close()
        app.logger.info("Bootstrapped database from seed.sql")
    except OSError as exc:
        app.logger.warning(
            "Could not bootstrap %s from seed.sql (%s); starting empty.", path, exc
        )


def _run_migrations():
    """Lightweight in-place schema fixes for existing SQLite files."""
    engine = db.engine
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='interview_round'"
            )
        ).fetchone()
        if not row or row[0] is None:
            return
        create_sql = row[0]
        # Drop the old UNIQUE(opportunity_id, round_number) constraint so a
        # rescheduled/cancelled round can coexist with its replacement.
        if "uq_round_per_opp" not in create_sql and "UNIQUE" not in create_sql.upper():
            return
        cols = [
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(interview_round)")
            ).fetchall()
        ]
        col_list = ", ".join(cols)
        conn.execute(text("ALTER TABLE interview_round RENAME TO interview_round__old"))
        conn.execute(
            text(
                """
                CREATE TABLE interview_round (
                    id INTEGER NOT NULL PRIMARY KEY,
                    opportunity_id INTEGER NOT NULL REFERENCES opportunity(id),
                    round_number INTEGER NOT NULL DEFAULT 1,
                    scheduled_date DATE,
                    start_time TIME,
                    end_time TIME,
                    interview_mode VARCHAR(20),
                    status VARCHAR(20),
                    outcome VARCHAR(20),
                    sme_review TEXT,
                    ps_review TEXT,
                    is_final BOOLEAN,
                    notes TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                f"INSERT INTO interview_round ({col_list}) "
                f"SELECT {col_list} FROM interview_round__old"
            )
        )
        conn.execute(text("DROP TABLE interview_round__old"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_interview_round_opportunity_id "
                "ON interview_round (opportunity_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_interview_round_scheduled_date "
                "ON interview_round (scheduled_date)"
            )
        )


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------- #
# Small parsing helpers
# --------------------------------------------------------------------------- #
def parse_date(value):
    value = clean(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value):
    value = clean(value)
    if not value:
        return None
    for fmt in ("%H:%M", "%I:%M %p", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def parse_float(value):
    value = clean(value)
    if not value:
        return None
    try:
        return float(value.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def month_range(start: date, end: date):
    cur = date(start.year, start.month, 1)
    out = []
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return out


# --------------------------------------------------------------------------- #
# Dashboard statistics
# --------------------------------------------------------------------------- #
def build_stats(start: date | None, end: date | None):
    q = db.select(Opportunity)
    if start:
        q = q.filter(Opportunity.submission_date >= start)
    if end:
        q = q.filter(Opportunity.submission_date <= end)
    opps = list(db.session.execute(q).scalars())

    total_applied = len(opps)
    unique_ids = {o.id for o in opps}

    rounds = [r for o in opps for r in o.rounds]

    interviewed = sum(1 for o in opps if o.rounds)
    reached_r2 = sum(1 for o in opps if o.max_round >= 2)
    reached_r3 = sum(1 for o in opps if o.max_round >= 3)
    reached_final = sum(1 for o in opps if o.has_final)

    today = date.today()
    # Distinct rounds reached (a rescheduled/repeat entry for the same number
    # is not counted twice).
    distinct_rounds = sum(len(o.round_numbers) for o in opps)
    completed_rounds = sum(1 for r in rounds if r.status == "Completed")
    upcoming_rounds = sum(
        1 for r in rounds
        if r.status == "Scheduled" and r.scheduled_date and r.scheduled_date >= today
    )
    passed_rounds = sum(1 for r in rounds if r.outcome == "Pass")

    def name_counter(attr):
        c = Counter()
        for o in opps:
            n = o.name(attr) or "Unspecified"
            c[n] += 1
        return c

    def top(counter, n=10):
        items = counter.most_common()
        labeled = [(k, v) for k, v in items if k != "Unspecified"]
        labeled = labeled[:n]
        return {"labels": [k for k, _ in labeled], "values": [v for _, v in labeled]}

    def full_breakdown(counter, order=None):
        if order:
            keys = [k for k in order if counter.get(k)]
            keys += [k for k in counter if k not in order]
        else:
            keys = [k for k, _ in counter.most_common()]
        return {"labels": keys, "values": [counter[k] for k in keys]}

    emp_counter = Counter((o.employment_type or "Unspecified") for o in opps)
    mode_counter = Counter((o.work_mode or "Unspecified") for o in opps)
    status_counter = Counter((o.status or "Unspecified") for o in opps)

    # -- monthly trends --------------------------------------------------
    sub_dates = [o.submission_date for o in opps if o.submission_date]
    sched_dates = [r.scheduled_date for r in rounds if r.scheduled_date]
    all_dates = sub_dates + sched_dates
    if all_dates:
        months = month_range(min(all_dates), max(max(all_dates), today))
    else:
        months = []

    sub_by_month = Counter(month_key(d) for d in sub_dates)
    intv_by_month = Counter(month_key(d) for d in sched_dates)
    completed_by_month = Counter(
        month_key(r.scheduled_date) for r in rounds
        if r.scheduled_date and r.status == "Completed"
    )

    cumulative = []
    running = 0
    for m in months:
        running += sub_by_month.get(m, 0)
        cumulative.append(running)

    return {
        "cards": {
            "total_applied": total_applied,
            "unique_opportunities": len(unique_ids),
            "interviewed": interviewed,
            "reached_r2": reached_r2,
            "reached_r3": reached_r3,
            "reached_final": reached_final,
            "total_rounds": distinct_rounds,
            "completed_rounds": completed_rounds,
            "upcoming_rounds": upcoming_rounds,
            "passed_rounds": passed_rounds,
        },
        "funnel": {
            "labels": ["Applied", "Interviewed", "Round 2+", "Round 3+", "Final round"],
            "values": [total_applied, interviewed, reached_r2, reached_r3, reached_final],
        },
        "employment": full_breakdown(emp_counter, EMPLOYMENT_TYPES + ["Unspecified"]),
        "work_mode": full_breakdown(mode_counter, WORK_MODES + ["Unspecified"]),
        "status": full_breakdown(status_counter),
        "vendors": top(name_counter("vendor")),
        "end_clients": top(name_counter("end_client")),
        "imp_partners": top(name_counter("imp_partner")),
        "prime_vendors": top(name_counter("prime_vendor")),
        "pods": full_breakdown(name_counter("pod")),
        "trend": {
            "months": months,
            "submissions": [sub_by_month.get(m, 0) for m in months],
            "cumulative_submissions": cumulative,
            "interviews": [intv_by_month.get(m, 0) for m in months],
            "completed": [completed_by_month.get(m, 0) for m in months],
        },
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def register_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if check_password_hash(app.config["APP_PASSWORD_HASH"], password):
                session["authed"] = True
                session.permanent = True
                dest = request.args.get("next") or url_for("dashboard")
                return redirect(dest)
            flash("Wrong password.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- dashboard ----------------------------------------------------- #
    @app.route("/")
    @login_required
    def dashboard():
        start = parse_date(request.args.get("start"))
        end = parse_date(request.args.get("end"))
        stats = build_stats(start, end)
        return render_template(
            "dashboard.html",
            stats=stats,
            start=request.args.get("start", ""),
            end=request.args.get("end", ""),
        )

    # ---- opportunities ----------------------------------------------- #
    @app.route("/opportunities")
    @login_required
    def opportunities():
        q = db.select(Opportunity)
        search = clean(request.args.get("q"))
        f_status = request.args.get("status", "")
        f_emp = request.args.get("employment_type", "")
        f_mode = request.args.get("work_mode", "")
        f_stage = request.args.get("stage", "")

        rows = list(db.session.execute(q.order_by(Opportunity.submission_date.desc())).scalars())

        def matches(o):
            if search:
                hay = " ".join(
                    filter(None, [
                        o.position_title,
                        o.name("vendor"),
                        o.name("end_client"),
                        o.name("imp_partner"),
                        o.name("prime_vendor"),
                        o.name("pod"),
                    ])
                ).lower()
                if search.lower() not in hay:
                    return False
            if f_status and (o.status or "") != f_status:
                return False
            if f_emp and (o.employment_type or "") != f_emp:
                return False
            if f_mode and (o.work_mode or "") != f_mode:
                return False
            if f_stage == "interviewed" and not o.rounds:
                return False
            if f_stage == "r2" and o.max_round < 2:
                return False
            if f_stage == "final" and not o.has_final:
                return False
            return True

        rows = [o for o in rows if matches(o)]
        statuses = sorted({o.status for o in db.session.execute(db.select(Opportunity)).scalars() if o.status})
        return render_template(
            "opportunities.html",
            rows=rows,
            statuses=statuses,
            employment_types=EMPLOYMENT_TYPES,
            work_modes=WORK_MODES,
            filters={
                "q": request.args.get("q", ""),
                "status": f_status,
                "employment_type": f_emp,
                "work_mode": f_mode,
                "stage": f_stage,
            },
        )

    @app.route("/opportunities/<int:opp_id>")
    @login_required
    def opportunity_detail(opp_id):
        opp = db.get_or_404(Opportunity, opp_id)
        return render_template("opportunity_detail.html", opp=opp)

    @app.route("/opportunities/new", methods=["GET", "POST"])
    @login_required
    def opportunity_new():
        if request.method == "POST":
            opp = Opportunity(source="manual")
            db.session.add(opp)
            _apply_opportunity_form(opp, request.form, include_csv_fields=True)
            db.session.commit()
            flash("Opportunity added.", "success")
            return redirect(url_for("opportunity_detail", opp_id=opp.id))
        return render_template(
            "opportunity_form.html",
            opp=None,
            **_form_choices(),
        )

    @app.route("/opportunities/<int:opp_id>/edit", methods=["GET", "POST"])
    @login_required
    def opportunity_edit(opp_id):
        opp = db.get_or_404(Opportunity, opp_id)
        if request.method == "POST":
            _apply_opportunity_form(
                opp, request.form, include_csv_fields=(opp.source == "manual")
            )
            db.session.commit()
            flash("Saved.", "success")
            return redirect(url_for("opportunity_detail", opp_id=opp.id))
        return render_template(
            "opportunity_form.html",
            opp=opp,
            **_form_choices(),
        )

    @app.route("/opportunities/<int:opp_id>/delete", methods=["POST"])
    @login_required
    def opportunity_delete(opp_id):
        opp = db.get_or_404(Opportunity, opp_id)
        db.session.delete(opp)
        db.session.commit()
        flash("Opportunity deleted.", "info")
        return redirect(url_for("opportunities"))

    # ---- CSV import -------------------------------------------------- #
    @app.route("/import", methods=["GET", "POST"])
    @login_required
    def import_view():
        if request.method == "POST":
            try:
                if "file" in request.files and request.files["file"].filename:
                    text = request.files["file"].read().decode("utf-8-sig")
                    summary = import_csv(text)
                else:
                    summary = import_csv_path(app.config["SUBMISSIONS_CSV"])
            except FileNotFoundError:
                flash("submissions.csv not found on the server.", "danger")
                return redirect(url_for("import_view"))
            except Exception as exc:  # noqa: BLE001 - surface parse errors to the user
                db.session.rollback()
                flash(f"Import failed: {exc}", "danger")
                return redirect(url_for("import_view"))
            flash(
                f"Import done: {summary['created']} added, "
                f"{summary['updated']} refreshed, {summary['skipped']} skipped.",
                "success",
            )
            return redirect(url_for("opportunities"))
        default_path = app.config["SUBMISSIONS_CSV"]
        return render_template(
            "import.html",
            default_path=default_path,
            default_exists=os.path.exists(default_path),
        )

    # ---- interview rounds ------------------------------------------- #
    @app.route("/opportunities/<int:opp_id>/rounds/new", methods=["GET", "POST"])
    @login_required
    def round_new(opp_id):
        opp = db.get_or_404(Opportunity, opp_id)
        next_round = opp.max_round + 1 if opp.rounds else 1
        if request.method == "POST":
            rnd = InterviewRound(opportunity_id=opp.id)
            db.session.add(rnd)
            _apply_round_form(rnd, request.form)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not save the round. Please check the values.", "danger")
                return redirect(url_for("round_new", opp_id=opp.id))
            flash(f"Round {rnd.round_number} added.", "success")
            return redirect(url_for("opportunity_detail", opp_id=opp.id))
        return render_template(
            "round_form.html",
            opp=opp,
            rnd=None,
            default_round=next_round,
            statuses=ROUND_STATUSES,
            outcomes=ROUND_OUTCOMES,
            interview_modes=INTERVIEW_MODES,
        )

    @app.route("/rounds/<int:round_id>/edit", methods=["GET", "POST"])
    @login_required
    def round_edit(round_id):
        rnd = db.get_or_404(InterviewRound, round_id)
        if request.method == "POST":
            _apply_round_form(rnd, request.form)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not save the round. Please check the values.", "danger")
                return redirect(url_for("round_edit", round_id=rnd.id))
            flash("Round updated.", "success")
            return redirect(url_for("opportunity_detail", opp_id=rnd.opportunity_id))
        return render_template(
            "round_form.html",
            opp=rnd.opportunity,
            rnd=rnd,
            default_round=rnd.round_number,
            statuses=ROUND_STATUSES,
            outcomes=ROUND_OUTCOMES,
            interview_modes=INTERVIEW_MODES,
        )

    @app.route("/rounds/<int:round_id>/reschedule", methods=["GET", "POST"])
    @login_required
    def round_reschedule(round_id):
        rnd = db.get_or_404(InterviewRound, round_id)
        if request.method == "POST":
            hist = RescheduleHistory(
                interview_round_id=rnd.id,
                old_date=rnd.scheduled_date,
                old_start_time=rnd.start_time,
                old_end_time=rnd.end_time,
                new_date=parse_date(request.form.get("scheduled_date")),
                new_start_time=parse_time(request.form.get("start_time")),
                new_end_time=parse_time(request.form.get("end_time")),
                reason=clean(request.form.get("reason")),
            )
            db.session.add(hist)
            rnd.scheduled_date = hist.new_date
            rnd.start_time = hist.new_start_time
            rnd.end_time = hist.new_end_time
            if rnd.status in ("Scheduled", "Rescheduled"):
                rnd.status = "Rescheduled"
            db.session.commit()
            flash("Round rescheduled; previous slot saved to history.", "success")
            return redirect(url_for("opportunity_detail", opp_id=rnd.opportunity_id))
        return render_template("round_reschedule.html", rnd=rnd)

    @app.route("/rounds/<int:round_id>/delete", methods=["POST"])
    @login_required
    def round_delete(round_id):
        rnd = db.get_or_404(InterviewRound, round_id)
        opp_id = rnd.opportunity_id
        db.session.delete(rnd)
        db.session.commit()
        flash("Round deleted.", "info")
        return redirect(url_for("opportunity_detail", opp_id=opp_id))

    # ---- ask-the-data chatbot -------------------------------------- #
    @app.route("/ask", methods=["GET"])
    @login_required
    def ask_view():
        if not app.config.get("OPENAI_API_KEY"):
            flash("Set OPENAI_API_KEY to enable the Ask feature.", "warning")
            return redirect(url_for("dashboard"))
        return render_template(
            "ask.html",
            history=session.get("chat_history", []),
            model=app.config["OPENAI_MODEL"],
        )

    @app.route("/ask/query", methods=["POST"])
    @login_required
    def ask_query():
        if not app.config.get("OPENAI_API_KEY"):
            return {"ok": False, "error": "Chatbot is not configured."}, 503
        question = (request.json or {}).get("question", "") if request.is_json \
            else request.form.get("question", "")
        bot = _get_chatbot(app)
        history = session.get("chat_history", [])
        result = bot.ask(question, history)
        if result.get("ok"):
            history = history + [
                {"question": result["question"], "sql": result["sql"],
                 "answer": result["answer"]}
            ]
            session["chat_history"] = history[-8:]
        return result

    @app.route("/ask/clear", methods=["POST"])
    @login_required
    def ask_clear():
        session.pop("chat_history", None)
        return redirect(url_for("ask_view"))


# --------------------------------------------------------------------------- #
# Chatbot (lazy singleton)
# --------------------------------------------------------------------------- #
_CHATBOT = None


def _get_chatbot(app):
    global _CHATBOT
    if _CHATBOT is None:
        from chatbot import Chatbot

        _CHATBOT = Chatbot(
            api_key=app.config["OPENAI_API_KEY"],
            model=app.config["OPENAI_MODEL"],
            db_uri=app.config["SQLALCHEMY_DATABASE_URI"],
            base_url=app.config.get("OPENAI_BASE_URL"),
        )
    return _CHATBOT


# --------------------------------------------------------------------------- #
# Form application helpers
# --------------------------------------------------------------------------- #
_DIM_FORM_FIELDS = {
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


def _form_choices():
    def names(model):
        return sorted(
            n for (n,) in db.session.execute(db.select(model.name)).all()
        )

    return {
        "employment_types": EMPLOYMENT_TYPES,
        "pay_rate_types": PAY_RATE_TYPES,
        "work_modes": WORK_MODES,
        "dim_options": {key: names(model) for key, model in _DIM_FORM_FIELDS.items()},
    }


def _apply_opportunity_form(opp: Opportunity, form, include_csv_fields: bool):
    if include_csv_fields:
        for key, model in _DIM_FORM_FIELDS.items():
            obj = get_or_create(model, form.get(key))
            setattr(opp, f"{key}_id", obj.id if obj else None)
        opp.status = clean(form.get("status"))
        opp.submission_date = parse_date(form.get("submission_date"))
        opp.confirmation_date = parse_date(form.get("confirmation_date"))
        opp.actual_start = parse_date(form.get("actual_start"))
        opp.position_title = clean(form.get("position_title"))
        opp.duration = clean(form.get("duration"))
        if opp.dedupe_key is None:
            opp.dedupe_key = f"manual:{datetime.now().timestamp()}"

    # manual fields (always editable, whatever the source)
    opp.job_description = clean(form.get("job_description"))
    opp.employment_type = clean(form.get("employment_type"))
    opp.pay_rate_type = clean(form.get("pay_rate_type"))
    opp.pay_min = parse_float(form.get("pay_min"))
    opp.pay_max = parse_float(form.get("pay_max"))
    opp.pay_currency = clean(form.get("pay_currency")) or "USD"
    opp.comp_notes = clean(form.get("comp_notes"))
    opp.work_mode = clean(form.get("work_mode"))
    opp.onsite_city = clean(form.get("onsite_city"))
    opp.onsite_state = clean(form.get("onsite_state"))


def _apply_round_form(rnd: InterviewRound, form):
    raw_round = form.get("round_number")
    try:
        rnd.round_number = max(0, int(raw_round))
    except (TypeError, ValueError):
        rnd.round_number = rnd.round_number if rnd.round_number is not None else 1
    rnd.scheduled_date = parse_date(form.get("scheduled_date"))
    rnd.start_time = parse_time(form.get("start_time"))
    rnd.end_time = parse_time(form.get("end_time"))
    rnd.interview_mode = clean(form.get("interview_mode"))
    rnd.status = clean(form.get("status")) or "Scheduled"
    rnd.outcome = clean(form.get("outcome")) or "Pending"
    rnd.sme_review = clean(form.get("sme_review"))
    rnd.ps_review = clean(form.get("ps_review"))
    rnd.is_final = bool(form.get("is_final"))
    rnd.notes = clean(form.get("notes"))


# --------------------------------------------------------------------------- #
# Jinja helpers
# --------------------------------------------------------------------------- #
def register_jinja(app):
    @app.template_filter("d")
    def _fmt_date(value):
        if not value:
            return "—"
        return value.strftime("%d %b %Y")

    @app.template_filter("t")
    def _fmt_time(value):
        if not value:
            return ""
        return value.strftime("%I:%M %p").lstrip("0")

    @app.context_processor
    def _inject():
        return {
            "today": date.today(),
            "chatbot_enabled": bool(app.config.get("OPENAI_API_KEY")),
        }


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
