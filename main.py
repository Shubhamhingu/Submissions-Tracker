"""Convenience entry point: `python main.py` runs the web app.

For database setup use `manage.py` (initdb / import / set-password).
"""
from app import app


def main():
    app.run(debug=True)


if __name__ == "__main__":
    main()
