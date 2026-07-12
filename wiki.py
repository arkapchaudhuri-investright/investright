"""Wikidata person enrichment for the Leadership grid (free, keyless).

Adds photo / education / one-line bio to yfinance's bare officer list — the
coverage is honest: famous execs (CEOs, chairs) resolve, most lieutenants
don't, and the page falls back to an initials monogram + base fields. Called
only by the nightly refresh (cron writes, web reads), one person at a time
with politeness sleeps, and a person is only re-queried while un-resolved.
"""
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

EXEC_DIR = Path(__file__).parent / "static" / "execs"
TIMEOUT = 12
_UA = {"User-Agent": "InvestRight/1.0 (leadership enrichment)"}
# The Wikidata search hit must *describe* a business person, or we don't trust
# the match — "Tim Cook" must not resolve to a chef.
_ROLE_WORDS = ("executive", "business", "chairman", "chairperson", "chief",
               "entrepreneur", "industrialist", "banker", "director",
               "officer", "manager", "founder", "billionaire", "investor")


def _clean_name(name):
    """Drop yfinance's honorifics ("Mr. Timothy D. Cook") + doubled spaces."""
    n = re.sub(r"^\s*(mr|ms|mrs|dr|prof|sir)\.?\s+", "", name or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def _api(params):
    return requests.get("https://www.wikidata.org/w/api.php", params={
        **params, "format": "json"}, timeout=TIMEOUT, headers=_UA).json()


def enrich_person(name):
    """{'photo_url', 'edu', 'bio'} for a business person, or None when the
    match isn't confident. Raises nothing — callers treat errors as transient."""
    q = _clean_name(name)
    if not q:
        return None
    hits = _api({"action": "wbsearchentities", "search": q, "language": "en",
                 "type": "item", "limit": 5}).get("search", [])
    hit = next((h for h in hits
                if any(w in (h.get("description") or "").lower()
                       for w in _ROLE_WORDS)), None)
    if not hit:
        return None
    ent = _api({"action": "wbgetentities", "ids": hit["id"],
                "props": "claims"}).get("entities", {}).get(hit["id"], {})
    claims = ent.get("claims", {})

    def vals(pid):
        out = []
        for c in claims.get(pid, []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v is not None:
                out.append(v)
        return out

    photos = [v for v in vals("P18") if isinstance(v, str)]
    edu_ids = [v.get("id") for v in vals("P69") if isinstance(v, dict) and v.get("id")]
    edu = []
    if edu_ids:
        time.sleep(0.5)
        ents = _api({"action": "wbgetentities", "ids": "|".join(edu_ids[:4]),
                     "props": "labels", "languages": "en"}).get("entities", {})
        for qid in edu_ids[:4]:
            lbl = ents.get(qid, {}).get("labels", {}).get("en", {}).get("value")
            if lbl:
                edu.append(lbl)
    return {
        "photo_url": ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                      + quote(photos[0]) + "?width=256") if photos else None,
        "edu": edu,
        "bio": (hit.get("description") or "").strip() or None,
    }


def cache_photo(key, url):
    """Download a Commons portrait to static/execs/<key>.jpg; filename or None."""
    if not url:
        return None
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=_UA)
        if r.status_code == 200 and "image" in r.headers.get("content-type", "") \
                and len(r.content) > 2000:
            dest = EXEC_DIR / (safe + ".jpg")
            dest.write_bytes(r.content)
            return dest.name
    except Exception:
        pass
    return None
