"""Application configuration.

All secrets are read from environment variables so nothing sensitive lands in
git.  For local use sensible defaults are provided; override them in production
(PythonAnywhere -> Web tab -> Environment variables, or a .env style wrapper).

For local use, copy ``.env.example`` to ``.env`` and fill in the values -- that
file is git-ignored and loaded automatically here.  In production set real
environment variables instead (PythonAnywhere Web tab, Render dashboard, ...).

To set the login password, generate a hash once:

    python -c "from werkzeug.security import generate_password_hash as g; print(g('your-password'))"

and put it in ``.env`` as APP_PASSWORD_HASH=...
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Load a local .env file if present (no-op when python-dotenv isn't installed
# or the file is missing -- e.g. in production where real env vars are used).
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ModuleNotFoundError:
    pass


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Where the SQLite file lives.  Override with DATABASE_URL if needed.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "count_automation.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Single-password gate.  Default password is "changeme" until you set your own.
    APP_PASSWORD_HASH = os.environ.get(
        "APP_PASSWORD_HASH",
        # hash of "changeme" -- replace via the APP_PASSWORD_HASH env var
        "scrypt:32768:8:1$2gYq4DEwrTSo8uta$d3948ddcd043f703026c37ab2c9e217534f677e2f1bd9f286f25090929a70291e619f8713f92d64a4f53f2e27db88d091feaf05efdf1f1bb1886a90826848f04",
    )

    # Default path to the submissions CSV used by the "import from project file" button.
    SUBMISSIONS_CSV = os.environ.get(
        "SUBMISSIONS_CSV", os.path.join(BASE_DIR, "submissions.csv")
    )

    # --- Ask-the-data chatbot (text-to-SQL) --------------------------------
    # The "Ask" tab only appears when OPENAI_API_KEY is set.
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    # Optional: point at an OpenAI-compatible endpoint (Azure, a proxy, etc.).
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "") or None
