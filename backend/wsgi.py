"""WSGI entrypoint for production hosts (Render, gunicorn, etc.).

Running the app is the same everywhere: `gunicorn "backend.wsgi:app"`.
Environment comes from the host's real env vars — on Render the dashboard's
Environment tab — and/or a local `.env` (loaded via python-dotenv, a no-op
when absent). Keep secret-holding `.env` files out of git.
"""
from backend.app import create_app
from backend.config import Config

app = create_app(Config.from_env())