"""Convenience entry point: `python main.py` runs the web app.

For database setup use `manage.py` (initdb / import / set-password).
"""
from app import app


def main():
    # app.run(debug=True)
    app.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
