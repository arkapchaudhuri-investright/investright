"""User analytics — offline geolocation, bot tagging, and sessionisation.

This is the nightly ENRICHMENT layer for the raw `events` table. It never runs
in a web request: `build()` rides the refresh cron, writes the `geo_cache` and
`sessions` caches, and the /admin/analytics dashboard only reads them (the
app's cron-writes / web-reads rule).

What it produces:
  • geo_cache  — every distinct IP → city/region/country + a bot flag, resolved
                 offline against a DB-IP City Lite database (downloaded once to
                 data/; no visitor IP ever leaves the box).
  • sessions   — events grouped into visits (30-min inactivity gap) with a
                 duration, page count, entry/exit path, and funnel milestones.

Time-on-page is the server-side estimate: a page's dwell is the gap to the next
event in the same visit (the last page of a visit has no measurable dwell).
"""
import gzip
import ipaddress
import os
import shutil
import urllib.request
from datetime import datetime, timezone

from db import get_conn

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
GEODB_PATH = os.path.join(DATA_DIR, "dbip-city-lite.mmdb")
# DB-IP publishes a free monthly City-Lite mmdb (CC-BY 4.0), no account needed.
GEODB_URL = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"

SESSION_GAP_S = 30 * 60          # a >30-min silence starts a new visit
DWELL_CAP_S = 30 * 60           # cap one page's counted dwell (idle tabs, lunch)

# Bot heuristics — coarse but effective. UA substrings + a few datacenter/crawler
# IP prefixes we've actually seen. This only *labels*; nothing is dropped.
BOT_UA = ("bot", "crawl", "spider", "slurp", "bingpreview", "facebookexternalhit",
          "headless", "python-requests", "curl", "wget", "httpclient", "scan",
          "monitor", "uptime", "lighthouse", "pagespeed", "semrush", "ahrefs")
BOT_IP_PREFIX = ("66.249.",)   # Googlebot

# Engagement MILESTONES, ordered by depth of intent — deliberately NOT a
# sequential funnel. Search engines land people straight on /stock/<ticker>, so
# "opened a deep-dive" routinely outruns "searched", and a returning account
# holder signs in without saving anything. Treating these as funnel steps
# produced nonsense drop-off figures (−97%), so each is reported independently
# as "% of sessions that ever did this".
FUNNEL = [
    ("visit",    "Landed",              lambda e: True),
    ("deepdive", "Opened a deep-dive",  lambda e: (e["path"] or "").startswith("/stock/")),
    ("search",   "Searched / analyzed", lambda e: e["action"] in ("analyze", "compare")
                                                  or (e["path"] or "").startswith("/analyze")),
    ("signup",   "Signed in",           lambda e: e["user_id"] is not None),
    ("save",     "Saved a stock",       lambda e: e["action"] in ("add", "hold_add", "peer_add",
                                                                   "hold_import")),
]


# ─────────────────────────── geolocation ────────────────────────────
def ensure_geodb():
    """Return a path to the DB-IP City mmdb, downloading it once if absent.
    Tries the current month then the previous two (start-of-month lag). Returns
    None if it can't be fetched — geolocation then degrades to bot-flag only."""
    if os.path.exists(GEODB_PATH):
        return GEODB_PATH
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    months = []
    y, m = now.year, now.month
    for _ in range(3):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for ym in months:
        try:
            tmp = GEODB_PATH + ".gz"
            urllib.request.urlretrieve(GEODB_URL.format(ym=ym), tmp)
            with gzip.open(tmp, "rb") as fi, open(GEODB_PATH, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.remove(tmp)
            return GEODB_PATH
        except Exception:
            continue
    return None


def _is_private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True                      # unparseable → treat as non-public


def _bot_reason(ip, ua):
    ua = (ua or "").lower()
    if _is_private(ip):
        return "localhost/private"
    if any(ip.startswith(p) for p in BOT_IP_PREFIX):
        return "googlebot"
    if any(tok in ua for tok in BOT_UA):
        return "ua"
    return None


def geolocate_new(conn):
    """Resolve every distinct IP in `events` not yet in geo_cache. Bot flag is
    always set; city/country filled when the mmdb is available. Returns count."""
    try:
        import maxminddb
    except Exception:
        maxminddb = None
    reader = None
    if maxminddb:
        path = ensure_geodb()
        if path:
            try:
                reader = maxminddb.open_database(path)
            except Exception:
                reader = None

    todo = conn.execute(
        "SELECT DISTINCT e.ip, "
        "  (SELECT ua FROM events WHERE ip = e.ip AND ua IS NOT NULL LIMIT 1) ua "
        "FROM events e WHERE e.ip IS NOT NULL AND e.ip != '' "
        "AND e.ip NOT IN (SELECT ip FROM geo_cache)").fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for row in todo:
        ip, ua = row["ip"], row["ua"]
        reason = _bot_reason(ip, ua)
        city = region = country = cc = None
        if reader and not _is_private(ip):
            try:
                rec = reader.get(ip) or {}
                country = (rec.get("country", {}).get("names", {}) or {}).get("en")
                cc = rec.get("country", {}).get("iso_code")
                subs = rec.get("subdivisions") or []
                region = subs[0]["names"].get("en") if subs else None
                city = (rec.get("city", {}).get("names", {}) or {}).get("en")
            except (ValueError, KeyError):
                pass
        conn.execute(
            "INSERT OR REPLACE INTO geo_cache "
            "(ip, city, region, country, country_code, is_bot, bot_reason, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ip, city, region, country, cc, 1 if reason else 0, reason, now))
        n += 1
    if reader:
        reader.close()
    return n


# ─────────────────────────── sessionisation ─────────────────────────
def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def rebuild_sessions(conn):
    """Rebuild the `sessions` cache from scratch: order every event by visitor
    then time, cut a new visit on a >30-min gap, and roll up duration, pages,
    entry/exit, funnel milestones, and whether the visit was a bot. Returns the
    number of sessions written."""
    bots = {r["ip"]: r["is_bot"] for r in conn.execute("SELECT ip, is_bot FROM geo_cache")}
    rows = conn.execute(
        "SELECT ts, visitor, name, market, action, path, ua, ip, user_id "
        "FROM events WHERE visitor IS NOT NULL ORDER BY visitor, ts").fetchall()

    conn.execute("DELETE FROM sessions")
    sessions = []
    cur = None

    def flush(s):
        if not s:
            return
        start, end = s["events"][0], s["events"][-1]
        t0, t1 = _parse(start["ts"]), _parse(end["ts"])
        dur = int((t1 - t0).total_seconds()) if (t0 and t1) else 0
        sid = f"{s['visitor']}:{start['ts']}"
        milestones = {k: 0 for k, *_ in FUNNEL}
        for ev in s["events"]:
            for key, _label, pred in FUNNEL:
                if pred(ev):
                    milestones[key] = 1
        # a visit's identity fields = the richest non-null seen in it
        name = next((e["name"] for e in reversed(s["events"]) if e["name"]), None)
        uid = next((e["user_id"] for e in s["events"] if e["user_id"] is not None), None)
        market = next((e["market"] for e in reversed(s["events"]) if e["market"]), None)
        is_bot = 1 if bots.get(start["ip"], 0) or _bot_reason(start["ip"], start["ua"]) else 0
        sessions.append((
            sid, s["visitor"], start["ts"], end["ts"], dur, len(s["events"]),
            start["path"], end["path"], start["ip"], name, market,
            1 if uid is not None else 0, uid, is_bot,
            milestones["search"], milestones["deepdive"], milestones["save"],
            milestones["signup"]))

    for e in rows:
        e = dict(e)
        if cur and e["visitor"] == cur["visitor"]:
            prev_t, this_t = _parse(cur["events"][-1]["ts"]), _parse(e["ts"])
            if prev_t and this_t and (this_t - prev_t).total_seconds() <= SESSION_GAP_S:
                cur["events"].append(e)
                continue
        flush(cur)
        cur = {"visitor": e["visitor"], "events": [e]}
    flush(cur)

    conn.executemany(
        "INSERT INTO sessions (id, visitor, started_at, ended_at, duration_s, pages, "
        "entry_path, exit_path, ip, name, market, signed_in, user_id, is_bot, "
        "hit_search, hit_deepdive, hit_save, hit_signup) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sessions)
    return len(sessions)


def build():
    """Nightly entry point: geolocate new IPs, then rebuild sessions. Best-effort
    and self-contained; safe to call from refresh.py. Returns a status string."""
    with get_conn() as conn:
        geo = geolocate_new(conn)
        sess = rebuild_sessions(conn)
    return f"geo +{geo} · sessions {sess}"


# ─────────────────────────── dashboard queries ──────────────────────
def _where_bot(include_bots):
    # Always qualified to the sessions row (s) — geo_cache also has an is_bot,
    # so an unqualified name is ambiguous wherever the two are joined.
    return "" if include_bots else " AND s.is_bot = 0"


def overview(conn, include_bots=False):
    """Headline KPIs over sessions (humans-only unless include_bots)."""
    wb = _where_bot(include_bots)
    row = conn.execute(
        f"SELECT COUNT(*) sessions, COUNT(DISTINCT visitor) visitors, "
        f"  SUM(signed_in) signed_sessions, "
        f"  AVG(duration_s) avg_dur, AVG(pages) avg_pages, "
        f"  SUM(CASE WHEN pages <= 1 THEN 1 ELSE 0 END) bounces "
        f"FROM sessions s WHERE 1=1{wb}").fetchone()
    total_sessions = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    bot_sessions = conn.execute("SELECT COUNT(*) c FROM sessions WHERE is_bot=1").fetchone()["c"]
    d = dict(row)
    d["bot_sessions"] = bot_sessions
    d["human_sessions"] = total_sessions - bot_sessions
    d["bounce_rate"] = (100 * d["bounces"] / d["sessions"]) if d["sessions"] else 0
    return d


def funnel(conn, include_bots=False):
    """Engagement milestones: how many sessions ever reached each one, as a share
    of all sessions. Independent measures, not sequential steps — see FUNNEL for
    why drop-off between them would be meaningless here."""
    wb = _where_bot(include_bots)
    cols = {
        "visit":    "COUNT(*)",
        "search":   "SUM(hit_search)",
        "deepdive": "SUM(hit_deepdive)",
        "save":     "SUM(hit_save)",
        "signup":   "SUM(hit_signup)",
    }
    row = conn.execute(
        f"SELECT {', '.join(f'{expr} AS {k}' for k, expr in cols.items())} "
        f"FROM sessions s WHERE 1=1{wb}").fetchone()
    top = row["visit"] or 0
    return [{"key": key, "label": label, "count": row[key] or 0,
             "pct_top": (100 * (row[key] or 0) / top) if top else 0}
            for key, label, _ in FUNNEL]


def recent_sessions(conn, include_bots=False, limit=100):
    """Most recent visits with geo joined, newest first."""
    wb = _where_bot(include_bots)
    rows = conn.execute(
        f"SELECT s.*, g.city, g.region, g.country, g.country_code, g.bot_reason "
        f"FROM sessions s LEFT JOIN geo_cache g ON g.ip = s.ip "
        f"WHERE 1=1{wb} ORDER BY s.started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def session_journey(conn, session_id):
    """The ordered page-by-page journey for one visit, with per-page dwell (the
    gap to the next event, capped; last page = None). Returns (meta, steps)."""
    s = conn.execute(
        "SELECT s.*, g.city, g.region, g.country, g.country_code, g.bot_reason "
        "FROM sessions s LEFT JOIN geo_cache g ON g.ip = s.ip WHERE s.id = ?",
        (session_id,)).fetchone()
    if not s:
        return None, []
    s = dict(s)
    evs = conn.execute(
        "SELECT ts, action, path, ticker, ua FROM events "
        "WHERE visitor = ? AND ts >= ? AND ts <= ? ORDER BY ts",
        (s["visitor"], s["started_at"], s["ended_at"])).fetchall()
    steps = []
    evs = [dict(e) for e in evs]
    for i, e in enumerate(evs):
        dwell = None
        if i + 1 < len(evs):
            a, b = _parse(e["ts"]), _parse(evs[i + 1]["ts"])
            if a and b:
                dwell = min(int((b - a).total_seconds()), DWELL_CAP_S)
        e["dwell_s"] = dwell
        steps.append(e)
    return s, steps


def geo_breakdown(conn, include_bots=False, by="country", limit=40):
    """Distinct-visitor + session counts grouped by country or city."""
    wb = _where_bot(include_bots)
    grp = "g.country" if by == "country" else \
          "COALESCE(g.city,'(unknown)') || ', ' || COALESCE(g.country,'')"
    rows = conn.execute(
        f"SELECT {grp} place, COUNT(DISTINCT s.visitor) visitors, COUNT(*) sessions "
        f"FROM sessions s LEFT JOIN geo_cache g ON g.ip = s.ip "
        f"WHERE 1=1{wb} GROUP BY place ORDER BY visitors DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def top_pages(conn, include_bots=False, limit=20):
    """Most-viewed paths, with avg dwell (server estimate). Joins events→geo via
    ip to honour the bot filter."""
    wb = " AND COALESCE(g.is_bot,0) = 0" if not include_bots else ""
    rows = conn.execute(
        f"SELECT e.path, COUNT(*) views, COUNT(DISTINCT e.visitor) visitors "
        f"FROM events e LEFT JOIN geo_cache g ON g.ip = e.ip "
        f"WHERE e.path IS NOT NULL{wb} "
        f"GROUP BY e.path ORDER BY views DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
