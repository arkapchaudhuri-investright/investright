"""Shared pytest fixtures.

The whole point of the smoke suite is to run against a *throwaway* SQLite file,
never the real data/investright.db. db.get_conn() reads db.DB_PATH at call time,
so we repoint DB_PATH/DATA_DIR at a temp dir BEFORE importing app (app imports
db and calls init_db() at import time — see app.py). Everything downstream then
transparently uses the temp DB.
"""
import importlib
import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    """Import the Flask app with db pointed at a fresh temp SQLite file."""
    import db
    tmpdir = tmp_path_factory.mktemp("irdb")
    db.DATA_DIR = tmpdir
    db.DB_PATH = tmpdir / "test.db"
    # If app was already imported (e.g. another test module), drop it so its
    # import-time init_db() re-runs against the temp path.
    sys.modules.pop("app", None)
    app_mod = importlib.import_module("app")
    return app_mod


@pytest.fixture()
def client(app_module):
    app = app_module.app
    app.config.update(TESTING=True)
    # Local test cookies are set over http:// — don't require Secure.
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()
