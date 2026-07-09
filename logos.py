"""Company logos for the deep-dive header (Phase 9).

Fetched once at ingest — like every other external call (§8.0) — cached under
static/logos/ and served locally, so the page still renders with the network
down (offline-on-VM rule). Clearbit's free logo API was sunset, so we use two
favicon services that return a real brand mark; the always-available fallback is
a monogram of the company's initials, drawn in the template (no fetch needed).
"""
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

LOGO_DIR = Path(__file__).parent / "static" / "logos"
TIMEOUT = 12
# Below this many bytes a favicon service has almost certainly handed back a
# generic globe placeholder rather than the company's mark — treat as no logo.
_MIN_BYTES = 700


def _safe(ticker):
    return re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())


def find(ticker):
    """Filename of a cached logo for this ticker (e.g. 'AAPL.png'), or None."""
    for p in sorted(LOGO_DIR.glob(_safe(ticker) + ".*")):
        return p.name
    return None


def domain_of(website):
    """Bare domain from a yfinance website URL (drops scheme + www), or None."""
    if not website:
        return None
    host = urlparse(website if "//" in website else "//" + website).netloc
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _sources(domain):
    # (extension, url) in preference order. gstatic returns a sized PNG; the
    # DuckDuckGo icon is a solid ICO fallback.
    return [
        ("png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
                f"&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=128"),
        ("ico", f"https://icons.duckduckgo.com/ip3/{domain}.ico"),
    ]


def ensure(ticker, website):
    """Cache a logo for `ticker` if we don't already have one. Best-effort — any
    failure just leaves no file and the page falls back to the monogram. Returns
    the cached filename (e.g. 'AAPL.png') or None."""
    existing = find(ticker)
    if existing:
        return existing
    domain = domain_of(website)
    if not domain:
        return None
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    for ext, url in _sources(domain):
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "InvestRight/1.0 (logo cache)"})
            ctype = r.headers.get("content-type", "")
            if (r.status_code == 200 and ctype.startswith("image")
                    and len(r.content) >= _MIN_BYTES):
                dest = LOGO_DIR / (_safe(ticker) + "." + ext)
                dest.write_bytes(r.content)
                return dest.name
        except Exception:
            continue
    return None
