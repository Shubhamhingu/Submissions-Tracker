"""Ask-the-data chatbot: natural language -> SQL -> answer.

Flow for one question:
  1. Send the schema + a short data dictionary + the last few Q&A turns to the
     model and ask for a single read-only SQL statement.
  2. Validate it (one SELECT/WITH, no writes/PRAGMA/ATTACH), enforce a LIMIT,
     run it against a read-only SQLite connection.
  3. Send the question + SQL + rows back to the model for a one-sentence answer.

No LangChain: the schema is tiny, so a direct prompt is simpler to reason about
and to keep safe than a multi-step SQL agent.

Enable by setting OPENAI_API_KEY.  Uses the ``openai`` SDK (OpenAI-compatible;
OPENAI_BASE_URL lets you point elsewhere).
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlparse

MAX_ROWS = 200
MAX_HISTORY_TURNS = 5

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|analyze)\b",
    re.IGNORECASE,
)
_STARTS_OK = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)

DATA_DICTIONARY = """\
This is a personal job-application tracker.  Tables:

opportunity  -- one row per job the user was submitted to
  id, status (e.g. 'ACTIVE','CONFIRMED'), submission_date (DATE 'YYYY-MM-DD'),
  confirmation_date, actual_start, position_title, duration, job_description,
  csv_is_remote, source ('csv' | 'manual'),
  employment_type ('Full-time' | 'C2C' | 'W2' | '1099' | NULL),
  pay_rate_type ('Yearly' | 'Hourly' | NULL), pay_min, pay_max, pay_currency,
  comp_notes, work_mode ('Remote' | 'Onsite' | 'Hybrid' | NULL),
  onsite_city, onsite_state,
  and foreign keys: recruiter_id, marketing_profile_id, company_id,
  technology_id, end_client_id, vendor_id, imp_partner_id, prime_vendor_id,
  pod_id, vrm_id  -- each points at a lookup table below with columns (id, name)

lookup tables (id, name): recruiter, marketing_profile, company, technology,
  end_client, vendor, imp_partner (implementation partner), prime_vendor, pod,
  vrm

interview_round  -- one row per interview round for an opportunity
  id, opportunity_id, round_number (INTEGER; 0 = screening / recruiter call,
  then 1,2,3...), scheduled_date (DATE), start_time, end_time, interview_mode,
  status ('Scheduled' | 'Completed' | 'Rescheduled' | 'Cancelled' | 'No-show'),
  outcome ('Pending' | 'Pass' | 'Fail'),
  sme_review (TEXT, technical / subject-matter interviewer feedback),
  ps_review (TEXT, pod / recruiter / presales feedback),
  is_final (0/1 -- user ticked "this was the final round"), notes
  NOTE: an opportunity may have MORE THAN ONE row with the same round_number
  (e.g. a rescheduled attempt kept next to the one that happened).

reschedule_history  -- id, interview_round_id, old_date, old_start_time,
  old_end_time, new_date, new_start_time, new_end_time, reason, changed_at

Conventions & hints:
- Empty / unknown values ('-', 'TBD', 'N/A') are already stored as NULL.
- Company names given without a role (e.g. "TCS", "Kroger") may appear as a
  vendor, an implementation partner, a prime vendor, or an end client -- unless
  the user names the role, match against ALL of vendor, imp_partner,
  prime_vendor and end_client.  Match names case-insensitively with LIKE.
- "an interview" / "interviews given" usually means a DISTINCT opportunity that
  has at least one interview_round.  "rounds" means individual interview_round
  rows.  "reached round N" means an opportunity whose MAX(round_number) >= N.
- "applied" / "submissions" = rows in opportunity (optionally filtered by
  submission_date).
- Dates are ISO strings; use date(), strftime(), and BETWEEN on them.
- The user is the single person being marketed (marketing_profile), so you
  normally do not need to filter by it.
"""

SQL_SYSTEM_PROMPT = (
    "You translate questions about a SQLite database into ONE read-only SQL "
    "query. Output ONLY the SQL (no markdown fences, no commentary). Use a "
    "single SELECT or WITH statement. Never modify data. Prefer explicit JOINs "
    "to the lookup tables so you can filter/return names rather than ids. "
    "Keep result sets small and aggregated when the question asks 'how many'."
)


def _sqlite_path_from_uri(db_uri: str) -> str:
    if not db_uri.startswith("sqlite"):
        raise RuntimeError("The chatbot only supports SQLite databases.")
    # sqlite:///abs/or/rel/path.db  or  sqlite:////abs/path
    path = db_uri.split("sqlite:///", 1)[-1]
    if path.startswith("/") and urlparse(db_uri).netloc == "":
        pass
    return path


class Chatbot:
    def __init__(self, api_key: str, model: str, db_uri: str, base_url: str | None = None):
        from openai import OpenAI  # imported lazily so the app runs without the dep

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.db_path = _sqlite_path_from_uri(db_uri)
        self._schema_cache: str | None = None

    # -- schema --------------------------------------------------------- #
    def schema_sql(self) -> str:
        if self._schema_cache is None:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "AND sql IS NOT NULL ORDER BY name"
                ).fetchall()
            self._schema_cache = "\n\n".join(r[0].strip() for r in rows)
        return self._schema_cache

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, timeout=5
        )
        conn.row_factory = sqlite3.Row
        return conn

    # -- SQL generation ---------------------------------------------- #
    def _generate_sql(self, question: str, history: list[dict]) -> str:
        messages = [
            {"role": "system", "content": SQL_SYSTEM_PROMPT},
            {
                "role": "system",
                "content": f"Schema:\n{self.schema_sql()}\n\n{DATA_DICTIONARY}",
            },
        ]
        for turn in history[-MAX_HISTORY_TURNS:]:
            messages.append({"role": "user", "content": turn["question"]})
            messages.append({"role": "assistant", "content": turn.get("sql", "")})
        messages.append({"role": "user", "content": question})

        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        return _strip_sql(resp.choices[0].message.content or "")

    # -- answer phrasing ------------------------------------------- #
    def _phrase_answer(self, question: str, sql: str, rows: list[dict]) -> str:
        preview = json.dumps(rows[:50], default=str)
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Answer the user's question in one or two sentences using "
                        "the SQL result. Be precise with numbers. If the result is "
                        "empty, say so plainly. Do not show the SQL."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\nSQL: {sql}\nResult rows (JSON): {preview}"
                    ),
                },
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    # -- public -------------------------------------------------- #
    def ask(self, question: str, history: list[dict] | None = None) -> dict:
        history = history or []
        question = re.sub(r"\s+", " ", question).strip()
        if not question:
            return {"ok": False, "error": "Ask a question first."}

        try:
            sql = self._generate_sql(question, history)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": _short_err(exc)}

        ok, why = _validate_sql(sql)
        if not ok:
            return {"ok": False, "error": why, "sql": sql}
        sql = _enforce_limit(sql)

        try:
            rows, columns = self._run(sql)
        except sqlite3.Error as exc:
            # one retry, feeding the error back to the model
            try:
                sql = _strip_sql(
                    self._generate_sql(
                        f"{question}\n\n(The previous SQL failed with: {exc}. "
                        f"Return corrected SQL.)",
                        history,
                    )
                )
                ok, why = _validate_sql(sql)
                if not ok:
                    return {"ok": False, "error": why, "sql": sql}
                sql = _enforce_limit(sql)
                rows, columns = self._run(sql)
            except sqlite3.Error as exc2:
                return {"ok": False, "error": f"Query failed: {exc2}", "sql": sql}
            except Exception as exc2:  # noqa: BLE001
                return {"ok": False, "error": _short_err(exc2), "sql": sql}

        try:
            answer = self._phrase_answer(question, sql, rows)
        except Exception as exc:  # noqa: BLE001
            answer = f"(Could not phrase an answer: {exc})"

        return {
            "ok": True,
            "question": question,
            "answer": answer,
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "truncated": len(rows) >= MAX_ROWS,
        }

    def _run(self, sql: str):
        with self._connect() as conn:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(MAX_ROWS)
        rows = [dict(r) for r in fetched]
        return rows, columns


# --------------------------------------------------------------------------- #
# SQL guards
# --------------------------------------------------------------------------- #
def _short_err(exc: Exception) -> str:
    """Turn an OpenAI/SDK exception into one readable line."""
    name = exc.__class__.__name__
    if "AuthenticationError" in name:
        return "OpenAI rejected the API key. Check OPENAI_API_KEY."
    if "RateLimitError" in name:
        return "OpenAI rate limit or quota hit. Try again shortly."
    if "APIConnectionError" in name or "APITimeoutError" in name:
        return "Couldn't reach OpenAI (network/timeout). Try again."
    msg = getattr(exc, "message", None) or str(exc)
    msg = re.sub(r"\s+", " ", msg).strip()
    return f"Model call failed: {msg[:200]}"


def _strip_sql(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text.strip().rstrip(";").strip()


def _validate_sql(sql: str) -> tuple[bool, str]:
    if not sql:
        return False, "The model did not return a query."
    if ";" in sql:
        return False, "Only a single statement is allowed."
    if not _STARTS_OK.match(sql):
        return False, "Only SELECT / WITH queries are allowed."
    if _FORBIDDEN.search(sql):
        return False, "That query contains a disallowed keyword."
    return True, ""


def _enforce_limit(sql: str) -> str:
    if re.search(r"\blimit\b", sql, re.IGNORECASE):
        return sql
    return f"{sql}\nLIMIT {MAX_ROWS}"
