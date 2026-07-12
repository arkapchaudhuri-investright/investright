"""Company logos for the deep-dive header (Phase 9).

Fetched once at ingest — like every other external call (§8.0) — cached under
static/logos/ and served locally, so the page still renders with the network
down (offline-on-VM rule). Source order: Wikidata's official-logo property
(P154 — real brand marks, usually SVG, infinitely sharp) → a 256px favicon
service → DuckDuckGo's icon. Every candidate must pass a sharpness gate
(vector, or ≥64 real pixels) so a 16×16 favicon never ships blurry again.
The always-available fallback is a monogram of the company's initials, drawn
in the template (no fetch needed).
"""
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

LOGO_DIR = Path(__file__).parent / "static" / "logos"
TIMEOUT = 12
# Below this many bytes a favicon service has almost certainly handed back a
# generic globe placeholder rather than the company's mark — treat as no logo.
_MIN_BYTES = 700
# Sharpness gate: the header renders the mark at ~72 CSS px (×2 on retina), so
# anything under 64 real pixels arrives blurry (RELIANCE.NS once cached a
# 16×16 favicon). Vectors (SVG) always pass.
_MIN_PX = 64
_UA = {"User-Agent": "InvestRight/1.0 (logo cache)"}


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


# --- image sharpness ---------------------------------------------------------
def _png_px(b):
    if b[:8] != b"\x89PNG\r\n\x1a\n" or len(b) < 24:
        return None
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def _ico_px(b):
    """Largest icon in an ICO container (a 0 byte means 256)."""
    if len(b) < 22 or b[:4] != b"\x00\x00\x01\x00":
        return None
    best = 0
    for i in range(int.from_bytes(b[4:6], "little")):
        off = 6 + i * 16
        if off + 2 > len(b):
            break
        best = max(best, min(b[off] or 256, b[off + 1] or 256))
    return (best, best) if best else None


def _sharp(content, ctype):
    """Vector, or at least _MIN_PX on the short side. Unparseable → not sharp.
    The vector check sniffs for an actual <svg> root, not just any markup — a
    rate-limit HTML page must never pass as a logo."""
    if "svg" in ctype or b"<svg" in content[:512].lower():
        return True
    px = _png_px(content) or _ico_px(content)
    return bool(px) and min(px) >= _MIN_PX


def is_small(path):
    """True when a cached raster is under the sharpness gate (purge candidate)."""
    b = Path(path).read_bytes()
    return not _sharp(b, "svg" if str(path).endswith(".svg") else "")


# --- Wikidata / Wikipedia official logo ---------------------------------------
def _wiki_logo_urls(name):
    """HQ logo URL candidates via Wikidata, best first.

    Search entities by the company's full listed name ("RELIANCE INDUSTRIES
    LTD", "Apple Inc.") — that plus requiring the logo property keeps
    mismatches rare, and the sharpness gate catches anything that slips.
    Two candidates per matched entity:
      1. P154 official logo (Commons file, usually SVG — infinitely sharp)
      2. the enwiki article's page image at 512px (`pilicense=any` — catches
         fair-use logos Commons can't host, e.g. Reliance Industries')."""
    if not name:
        return []

    def _search(q):
        return requests.get(
            "https://www.wikidata.org/w/api.php", timeout=TIMEOUT, headers=_UA,
            params={"action": "wbsearchentities", "search": q, "language": "en",
                    "type": "item", "limit": 5, "format": "json"},
        ).json().get("search", [])

    hits = _search(name)
    if not hits:
        # Listing names carry legal suffixes Wikidata labels often drop
        # ("Oil and Natural Gas Corporation Limited" → no hits; without
        # "Limited" → the right entity). Retry once, stripped.
        stripped = re.sub(
            r",?\s+(incorporated|inc\.?|corporation|corp\.?|limited|ltd\.?|plc"
            r"|co\.?|company|holdings?)\s*$", "", name, flags=re.I).strip()
        if stripped and stripped != name:
            hits = _search(stripped)
    ids = [h["id"] for h in hits]
    if not ids:
        return []
    ents = requests.get(
        "https://www.wikidata.org/w/api.php", timeout=TIMEOUT, headers=_UA,
        params={"action": "wbgetentities", "ids": "|".join(ids),
                "props": "claims|sitelinks", "format": "json"},
    ).json().get("entities", {})
    out = []
    for qid in ids:                       # preserve search ranking
        ent = ents.get(qid, {})
        for c in ent.get("claims", {}).get("P154", []):
            f = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if f:
                ext = "svg" if f.lower().endswith(".svg") else "png"
                out.append((ext, "https://commons.wikimedia.org/wiki/"
                                 "Special:FilePath/" + quote(f)))
                break
        title = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
        if title:
            try:
                pages = requests.get(
                    "https://en.wikipedia.org/w/api.php", timeout=TIMEOUT,
                    headers=_UA,
                    params={"action": "query", "titles": title,
                            "prop": "pageimages", "pithumbsize": 512,
                            "pilicense": "any", "format": "json"},
                ).json().get("query", {}).get("pages", {})
                thumb = next(iter(pages.values())).get("thumbnail", {})
                if thumb.get("source"):
                    out.append(("png", thumb["source"]))
            except Exception:
                pass
        if out:                           # first entity with any candidate wins
            break
    return out


def _sources(domain, name):
    """(extension, url) candidates in preference order."""
    out = []
    try:
        out += _wiki_logo_urls(name)
    except Exception:
        pass
    if domain:
        out += [
            ("png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
                    f"&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=256"),
            ("ico", f"https://icons.duckduckgo.com/ip3/{domain}.ico"),
        ]
    return out


def ensure(ticker, website, name=None):
    """Cache a logo for `ticker` if we don't already have one. Best-effort — any
    failure just leaves no file and the page falls back to the monogram. Returns
    the cached filename (e.g. 'AAPL.svg') or None."""
    existing = find(ticker)
    if existing:
        return existing
    domain = domain_of(website)
    if not domain and not name:
        return None
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    for i, (ext, url) in enumerate(_sources(domain, name)):
        try:
            if i:                       # politeness gap — Wikimedia 429s rapid hits
                time.sleep(0.8)
            r = requests.get(url, timeout=TIMEOUT, headers=_UA)
            ctype = r.headers.get("content-type", "")
            # ctype must be a real image (rejects rate-limit HTML pages); the
            # byte floor skips vectors — a 500-byte SVG is a perfect logo.
            if (r.status_code == 200 and "image" in ctype
                    and (len(r.content) >= _MIN_BYTES or "svg" in ctype)
                    and _sharp(r.content, ctype)):
                dest = LOGO_DIR / (_safe(ticker) + "." + ext)
                dest.write_bytes(r.content)
                return dest.name
        except Exception:
            continue
    return None
