"""Small CLI helpers.

    python manage.py initdb              create tables
    python manage.py import [PATH]       import submissions CSV (default: config path)
    python manage.py set-password PW     print the APP_PASSWORD_HASH line to use
    python manage.py dump-seed           write seed.sql from the current database
"""
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

from app import app
from importer import import_csv_path
from models import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def dump_seed():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        print("dump-seed only works with a local SQLite database.")
        return
    src_path = uri[len("sqlite:///"):]
    out = os.path.join(BASE_DIR, "seed.sql")
    conn = sqlite3.connect(src_path)
    try:
        with open(out, "w", encoding="utf-8") as fh:
            for line in conn.iterdump():
                fh.write(line + "\n")
    finally:
        conn.close()
    print(f"Wrote {out} ({os.path.getsize(out)} bytes) from {src_path}")


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, *rest = argv

    if cmd == "set-password":
        if not rest:
            print("usage: python manage.py set-password YOUR_PASSWORD")
            return
        print("APP_PASSWORD_HASH=" + generate_password_hash(rest[0]))
        return

    with app.app_context():
        if cmd == "initdb":
            db.create_all()
            print("Tables created.")
        elif cmd == "import":
            path = rest[0] if rest else app.config["SUBMISSIONS_CSV"]
            summary = import_csv_path(path)
            print(f"Imported from {path}: {summary}")
        elif cmd == "dump-seed":
            dump_seed()
        else:
            print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
