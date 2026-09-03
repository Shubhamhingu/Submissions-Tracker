"""WSGI entry point for PythonAnywhere / gunicorn.

On PythonAnywhere: in the Web tab, set the WSGI configuration file to import
``application`` from this module, e.g.::

    import sys
    path = '/home/YOURUSER/Count_Automation'
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application

Set these environment variables in the Web tab:
    SECRET_KEY          - a long random string
    APP_PASSWORD_HASH   - output of werkzeug.security.generate_password_hash('yourpw')
"""
from app import app as application

if __name__ == "__main__":
    application.run()
