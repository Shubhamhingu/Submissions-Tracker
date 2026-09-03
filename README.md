# Count Automation

A personal web app to track job submissions, interview rounds and outcomes,
with a dashboard of trends and conversion metrics.

Stack: **Flask + SQLAlchemy + SQLite**, Bootstrap 5 and Chart.js on the front
end (both from CDN). Single-password login.

## What it does

* **Opportunities** — one row per job you were submitted to. CSV-sourced fields
  (recruiter, vendor, implementation partner, prime vendor, end client, pod,
  dates, title, duration) plus fields you fill in by hand:
  * **job description** — editable on every opportunity (CSV-sourced ones too);
    a CSV re-import never overwrites it
  * employment type — Full-time / C2C / W2 / 1099
  * pay — rate type (Yearly / Hourly) + min / max + currency + notes
  * work mode — Remote / Onsite / Hybrid + onsite city / state
* **Interview rounds** — round 0 (screening / recruiter call), 1, 2, 3, … per
  opportunity. Each has a schedule (date, start/end time, mode), a status
  (Scheduled / Completed / Rescheduled / Cancelled / No-show), an outcome
  (Pending / Pass / Fail), an **SME review** (technical / subject-matter
  feedback), a **PS review** (pod / recruiter feedback), a free-text note and an
  **"is final round"** checkbox. You can keep **more than one entry with the
  same round number** — e.g. a rescheduled/cancelled attempt kept alongside the
  one that actually happened. The dashboard counts distinct round numbers so
  these don't double-count.
* **Reschedule** — moving a round keeps its number and saves the old slot to a
  per-round history trail.
* **Dark mode** — toggle at the bottom of the sidebar; remembered per browser,
  defaults to your OS setting.
* **Ask the data** — a text-to-SQL chatbot. Ask "How many interviews have I
  given for TCS?" and it writes a read-only SQL query, runs it, and answers in
  plain English. Shows the SQL and the rows so you can verify. Remembers the
  last few turns for follow-ups ("and which passed?"). Only appears when
  `OPENAI_API_KEY` is set. See **Ask the data** below.
* **Dashboard** — KPI tiles, an Applied → Interviewed → R2 → R3 → Final funnel,
  Full-time vs C2C and Remote vs Onsite splits, top vendors / end clients /
  implementation partners / prime vendors, submissions by pod, submission
  status, and monthly + cumulative trend lines for submissions and interviews.
  A submission-date range filter at the top scopes every number and chart.

All text is whitespace-trimmed on import and on save; `-`, `TBD`, `N/A` and
blanks are stored as "no value".

## Normalized schema

Lookup tables (`recruiter`, `marketing_profile`, `company`, `technology`,
`end_client`, `vendor`, `imp_partner`, `prime_vendor`, `pod`, `vrm`) each hold a
name once. `opportunity` references them by foreign key and carries the manual
fields. `interview_round` belongs to an opportunity (no uniqueness on round
number — repeats are allowed); `reschedule_history` belongs to a round.

`app.py` runs a tiny startup migration that drops the old
`UNIQUE(opportunity_id, round_number)` constraint from existing SQLite files,
preserving all rows. It's a no-op once applied.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env             # then edit .env (see below)
python manage.py initdb          # create tables
python manage.py import          # load submissions.csv
python app.py                    # http://127.0.0.1:5000
```

### Configuration — use a `.env` file

Copy `.env.example` to `.env` and fill it in. `config.py` loads it
automatically; `.env` is git-ignored, so **never edit `config.py` for
secrets**. In production (PythonAnywhere / Render) set the same names as real
environment variables instead of shipping a `.env`.

| Variable | What |
|---|---|
| `SECRET_KEY` | any long random string (signs session cookies) |
| `APP_PASSWORD_HASH` | run `python manage.py set-password "your password"` and paste the printed value |
| `OPENAI_API_KEY` | only for the "Ask the data" tab; leave blank to hide it |
| `OPENAI_MODEL` | defaults to `gpt-4o-mini` |

Default password until you set `APP_PASSWORD_HASH`: **`changeme`**.

## CSV re-import

Use **Import CSV** in the app (upload a file, or import the server copy). Rows
are matched by `submission date + vendor + position title + end client`. New
rows are added; existing rows have only their CSV fields refreshed — your manual
fields and all interview data are never touched.

## Ask the data (chatbot)

Add your key to `.env` (or set it as a real env var) and restart:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini         # optional, this is the default
```

The tab stays hidden until `OPENAI_API_KEY` is present.

How it works (`chatbot.py`): the question + the DB schema + a short data
dictionary + your last few turns go to the model, which returns one SQL
statement. It's validated (must be a single `SELECT`/`WITH`, no writes / PRAGMA
/ ATTACH), a `LIMIT` is forced on, and it runs over a **read-only** SQLite
connection (`mode=ro`). The rows then go back to the model for a one-sentence
answer. No LangChain — the schema is small enough that a direct prompt is
simpler to keep safe.

Cost: ~$0.0002 per question with `gpt-4o-mini`.

**Hosting note:** PythonAnywhere's *free* tier blocks outbound calls to
`api.openai.com`, so the chatbot won't work on a free account (the rest of the
app does). Use a PythonAnywhere paid account, or host on Render (below), or just
run the app locally when you want to use the chatbot.

## Deploy on Render (Blueprint)

The repo ships a `render.yaml` Blueprint, so deploy is: push to GitHub → create
the Blueprint → paste two secrets → done.

**Why the Starter plan:** it uses a 1 GB persistent disk for the SQLite file so
your data survives restarts and redeploys. Render's *free* web services run on
an ephemeral filesystem — the database would reset every time the service spins
down or redeploys — so free is only OK if you never edit data on the live site.

### One-time

1. **Password hash** — locally:
   ```bash
   python manage.py set-password "your login password"
   ```
   Copy the printed `scrypt:...` value.
2. **Seed data** — `seed.sql` (committed in the repo) is loaded automatically the
   first time the database is empty, so the deployed app starts with your current
   opportunities and interview rounds. Refresh it before pushing with
   `python manage.py dump-seed` if you want the newest local data.
3. Push the repo to GitHub.

### In Render

4. Dashboard → **New** → **Blueprint** → connect the GitHub repo.
5. Render reads `render.yaml` and shows the service + disk. It prompts for the
   `sync: false` env vars:
   - `APP_PASSWORD_HASH` → the `scrypt:...` value from step 1
   - `OPENAI_API_KEY` → your key (or leave blank to hide the "Ask the data" tab)
6. **Apply**. First build installs deps, starts `gunicorn wsgi:application`, and
   `seed.sql` populates the disk. Open the `onrender.com` URL and log in.

### After it's live

Pick **one** place to enter data — the live site *or* local — since they don't
sync. To take a fresh local snapshot to the server later: `python manage.py
dump-seed`, commit, then in Render delete the disk and redeploy (this wipes the
server's data and reloads from `seed.sql`).

## Deploy on PythonAnywhere

1. Upload the project (or `git clone` it) to `/home/<you>/Count_Automation`.
2. Create a virtualenv and `pip install -r requirements.txt`.
3. Web tab → add a new **Flask** app (manual config), Python 3.11.
4. Edit the WSGI file to add the project to `sys.path` and
   `from wsgi import application` (see `wsgi.py` for the snippet).
5. Web tab → **Environment variables**: set `SECRET_KEY` and `APP_PASSWORD_HASH`.
6. Open a Bash console: `python manage.py initdb && python manage.py import`.
7. Reload the web app.

The SQLite file lives next to the code; back it up from the Files tab.
