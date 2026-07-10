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

from db import (LOGIN_MAX_PER_EMAIL, LOGIN_MAX_PER_IP, LOGIN_WINDOW_MIN,
                clear_login_failures, create_user, delete_user,
                get_user_by_email, get_user_by_id, login_failures,
                record_login_failure, rotate_session_token, set_password)

bp = Blueprint("auth", __name__)


# Hashed once at import. A miss on the email still pays for one hash check, so
# "no such account" and "wrong password" take the same time — otherwise the
# response latency tells an attacker which emails are registered.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def client_ip():
    """The visitor's IP. Behind Caddy the socket peer is always 127.0.0.1, so
    prefer the first hop of X-Forwarded-For (Caddy sets it; nothing else can
    reach gunicorn, which listens on loopback only)."""
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else request.remote_addr


def _start_session(user, remember=True):
    """Begin a logged-in session. `stok` pins it to the account's current
    session_token, so rotating that token (change-password) kills this session
    and every other one. session.clear() also drops the old CSRF token — it is
    re-minted on the next render."""
    session.clear()
    session["uid"] = user["id"]
    session["stok"] = user["session_token"]
    session.permanent = remember


def current_user():
    """The logged-in user's row as a dict, or None. Cached on flask.g per request
    so repeated calls (view + template) hit the DB once. Clears a stale session —
    one pointing at a deleted account, or carrying a session token that's been
    rotated out from under it (password changed elsewhere)."""
    if "user" in g.__dict__:
        return g.user
    uid = session.get("uid")
    g.user = None
    if uid:
        from db import get_conn
        with get_conn() as conn:
            row = get_user_by_id(conn, uid)
        if not row:
            session.pop("uid", None)      # account gone → drop the dead session
        elif row["session_token"] and session.get("stok") != row["session_token"]:
            session.clear()               # token rotated → this session is void
        else:
            g.user = dict(row)
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
                _start_session(user, remember=True)
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
        remember = bool(request.form.get("remember"))
        ip = client_ip()

        with get_conn() as conn:
            by_email, by_ip = login_failures(conn, email, ip)
            # Check the limit *before* touching the password, so a locked-out
            # guesser learns nothing and costs us no hashing work.
            if by_email >= LOGIN_MAX_PER_EMAIL or by_ip >= LOGIN_MAX_PER_IP:
                flash(f"Too many sign-in attempts. Wait {LOGIN_WINDOW_MIN} minutes "
                      "and try again.", "error")
                return render_template("login.html", email=email, remember=remember,
                                       next=request.form.get("next", "")), 429

            row = get_user_by_email(conn, email)
            ok = (check_password_hash(row["password_hash"], pw) if row
                  else (check_password_hash(_DUMMY_HASH, pw) and False))
            if ok:
                clear_login_failures(conn, email, ip)
            else:
                record_login_failure(conn, email, ip)

        if ok:
            # Remembered ⇒ a persistent cookie living PERMANENT_SESSION_LIFETIME
            # (30d). Otherwise a session cookie the browser drops on close —
            # the safer default on a shared machine (Tier C, §10.5).
            _start_session(row, remember=remember)
            flash(f"Signed in — welcome back{', ' + row['name'] if row['name'] else ''}.",
                  "ok")
            return redirect(_safe_next(request.form.get("next")))
        # Deliberately the same message whether the email exists or not.
        flash("Email or password didn't match. Try again.", "error")
        return render_template("login.html", email=email, remember=remember,
                               next=request.form.get("next", ""))
    return render_template("login.html", email="", remember=True,
                           next=request.args.get("next", ""))


@bp.post("/logout")
def logout():
    session.clear()                       # drops uid + stok + the CSRF token
    g.__dict__.pop("user", None)
    flash("Signed out.", "info")
    return redirect(url_for("home"))


@bp.get("/account")
@login_required
def account():
    """Account settings: change password (and, further down the page, the
    irreversible bits)."""
    return render_template("account.html", user=current_user())


@bp.post("/account/password")
@login_required
def change_password():
    from db import get_conn
    user = current_user()
    cur = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    new2 = request.form.get("new_password2") or ""

    if not check_password_hash(user["password_hash"], cur):
        err = "That isn't your current password."
    elif len(new) < 8:
        err = "Use a new password of at least 8 characters."
    elif new != new2:
        err = "The two new passwords don't match."
    elif new == cur:
        err = "That's already your password — pick a different one."
    else:
        err = None

    if err:
        flash(err, "error")
        return redirect(url_for("auth.account"))

    with get_conn() as conn:
        token = set_password(conn, user["id"], generate_password_hash(new))
    # Rotating the token voided *this* session too — re-pin it so the person who
    # just changed their password stays signed in, while other devices drop out.
    session["stok"] = token
    g.__dict__.pop("user", None)
    flash("Password changed. Any other devices have been signed out.", "ok")
    return redirect(url_for("auth.account"))


@bp.post("/account/signout-others")
@login_required
def signout_others():
    """Rotate the account's session token, which voids every session pinned to
    the old one — a lost phone, a library machine. This device is re-pinned to
    the new token, exactly as change_password does, so the person who clicked
    stays where they are. No password prompt: they're already authenticated, and
    demanding one here would only train people to type it more often."""
    from db import get_conn
    user = current_user()
    with get_conn() as conn:
        token = rotate_session_token(conn, user["id"])
    session["stok"] = token
    g.__dict__.pop("user", None)
    flash("Signed out on every other device. This one stays signed in.", "ok")
    return redirect(url_for("auth.account"))


@bp.post("/account/delete")
@login_required
def delete_account():
    """Irreversible. Guarded by the password *and* a typed confirmation, since a
    stray click here costs someone their watchlist and journal."""
    from db import get_conn
    user = current_user()
    pw = request.form.get("password") or ""
    typed = (request.form.get("confirm") or "").strip()

    if not check_password_hash(user["password_hash"], pw):
        flash("That isn't your password — account not deleted.", "error")
        return redirect(url_for("auth.account"))
    if typed != "DELETE":
        flash("Type DELETE in the box to confirm — account not deleted.", "error")
        return redirect(url_for("auth.account"))

    with get_conn() as conn:
        delete_user(conn, user["id"])
    session.clear()
    g.__dict__.pop("user", None)
    flash("Your account, watchlist and notes are gone. Thanks for trying InvestRight.",
          "info")
    return redirect(url_for("home"))
