"""Accounts for InvestRight (Phase 8, DESIGN §10).

Optional email + password login. The public site stays open (§10.0) — an
account only unlocks a per-user watchlist (Tier A) and notes (Tier B). No new
heavy deps: Werkzeug (PBKDF2) hashes passwords, Flask's signed-cookie session
holds login state. No OAuth, no email sending → emails are UNVERIFIED and there's
no self-serve password reset in v1 (§10.6); the register page says so.
"""
import functools

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import create_user, get_user_by_email, get_user_by_id

bp = Blueprint("auth", __name__)


def current_user():
    """The logged-in user's row as a dict, or None. Cached on flask.g per request
    so repeated calls (view + template) hit the DB once. Clears a stale session
    (uid pointing at a deleted account)."""
    if "user" in g.__dict__:
        return g.user
    uid = session.get("uid")
    g.user = None
    if uid:
        from db import get_conn
        with get_conn() as conn:
            row = get_user_by_id(conn, uid)
        if row:
            g.user = dict(row)
        else:
            session.pop("uid", None)      # account gone → drop the dead session
    return g.user


def login_required(view):
    """Gate a state-changing route behind login. Logged-out callers are bounced
    to /login with a friendly nudge; ?next= brings them back after signing in."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Sign in to save your watchlist — searching and Today are open to all.",
                  "info")
            # Return to the GET page the action came from (the watchlist routes are
            # POST-only, so `next` must not be the endpoint itself), else home.
            ref = request.referrer or ""
            nxt = ("/" + ref[len(request.host_url):]
                   if ref.startswith(request.host_url) else url_for("home"))
            return redirect(url_for("auth.login", next=nxt))
        return view(*args, **kwargs)
    return wrapped


@bp.app_context_processor
def inject_user():
    """Expose the current account to every template (base.html shows the state,
    home.html greets by the account name)."""
    return {"current_user": current_user()}


def _safe_next(raw):
    """Only allow same-site relative redirects (no scheme/host) — never an
    open redirect. Flask's full_path adds a trailing '?', trim a bare one."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw[:-1] if raw.endswith("?") else raw
    return url_for("home")


def _adopt_identity(conn, user):
    """Per-browser → account (§10.3): if the visitor typed a name/market before
    signing up and the account has none, adopt them. Cookies are percent-encoded
    client-side (base.html), so decode."""
    from urllib.parse import unquote
    name = unquote(request.cookies.get("ir_name") or "").strip() or None
    market = (unquote(request.cookies.get("ir_market") or "").strip() or None)
    updates = {}
    if name and not user.get("name"):
        updates["name"] = name
    if market and not user.get("market"):
        updates["market"] = market
    if updates:
        conn.execute("UPDATE users SET " + ",".join(f"{k}=?" for k in updates) +
                     " WHERE id=?", (*updates.values(), user["id"]))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        from db import get_conn
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        pw2 = request.form.get("password2") or ""
        name = (request.form.get("name") or "").strip() or None
        err = None
        if "@" not in email or "." not in email.split("@")[-1]:
            err = "That doesn't look like an email address."
        elif len(pw) < 8:
            err = "Use a password of at least 8 characters."
        elif pw != pw2:
            err = "The two passwords don't match."
        if not err:
            with get_conn() as conn:
                if get_user_by_email(conn, email):
                    err = "An account with that email already exists — try signing in."
                else:
                    uid = create_user(conn, email, generate_password_hash(pw),
                                      name=name,
                                      market=(request.cookies.get("ir_market") or None))
                    user = dict(get_user_by_id(conn, uid))
                    _adopt_identity(conn, user)
            if not err:
                session.clear()
                session["uid"] = uid
                session.permanent = True
                flash("Welcome to InvestRight — your account is ready.", "ok")
                return redirect(_safe_next(request.form.get("next")))
        flash(err, "error")
        return render_template("register.html", email=email, name=name or "",
                               next=request.form.get("next", ""))
    return render_template("register.html", email="", name="",
                           next=request.args.get("next", ""))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        from db import get_conn
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        with get_conn() as conn:
            row = get_user_by_email(conn, email)
        if row and check_password_hash(row["password_hash"], pw):
            session.clear()
            session["uid"] = row["id"]
            session.permanent = True
            flash(f"Signed in — welcome back{', ' + row['name'] if row['name'] else ''}.",
                  "ok")
            return redirect(_safe_next(request.form.get("next")))
        flash("Email or password didn't match. Try again.", "error")
        return render_template("login.html", email=email,
                               next=request.form.get("next", ""))
    return render_template("login.html", email="",
                           next=request.args.get("next", ""))


@bp.post("/logout")
def logout():
    session.pop("uid", None)
    g.__dict__.pop("user", None)
    flash("Signed out.", "info")
    return redirect(url_for("home"))
