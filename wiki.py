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
# Wikimedia throttles generic User-Agents hard; their policy wants a real client
# name + contact URL. A descriptive UA markedly cuts the 429s that were wiping
# photos (see _api's retry note).
_UA = {"User-Agent": "InvestRight/1.0 (https://investright.us; leadership photo "
                     "enrichment) python-requests"}
# The Wikidata search hit must *describe* a business person, or we don't trust
# the match — "Tim Cook" must not resolve to a chef.
_ROLE_WORDS = ("executive", "business", "chairman", "chairperson", "chief",
               "entrepreneur", "industrialist", "banker", "director",
               "officer", "manager", "founder", "co-founder", "billionaire",
               "investor", "ceo", "cfo", "president", "economist", "financier",
               "magnate")


def _enwiki_image(title):
    """Wikipedia page lead image for an article title, or None — a free photo
    source that often exists even when Wikidata has no P18 portrait."""
    if not title:
        return None
    try:
        pages = requests.get(
            "https://en.wikipedia.org/w/api.php", timeout=TIMEOUT, headers=_UA,
            params={"action": "query", "titles": title, "prop": "pageimages",
                    "piprop": "thumbnail", "pithumbsize": 256, "format": "json"},
        ).json().get("query", {}).get("pages", {})
        return next(iter(pages.values())).get("thumbnail", {}).get("source")
    except Exception:
        return None


def _clean_name(name):
    """Drop yfinance's honorifics ("Mr. Timothy D. Cook") + doubled spaces."""
    n = re.sub(r"^\s*(mr|ms|mrs|dr|prof|sir)\.?\s+", "", name or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def _api(params):
    """Wikidata API GET with a few polite retries. On persistent throttling it
    RAISES rather than returning empty — critical, because enrich_person treats
    "no hits" as a confident no-match and marks the row done. If a 429 looked
    like a no-match, a rate-limited night would permanently blank every exec
    photo (which is exactly what happened once). Raising keeps the row for a
    later retry instead."""
    last = None
    for attempt in range(3):
        try:
            r = requests.get("https://www.wikidata.org/w/api.php",
                             params={**params, "format": "json"},
                             timeout=TIMEOUT, headers=_UA)
            if r.status_code == 429:
                raise requests.HTTPError("Wikidata 429")
            return r.json()                       # ValueError if body is empty/HTML
        except (ValueError, requests.RequestException) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("Wikidata API unreachable")


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
                "props": "claims|sitelinks"}).get("entities", {}).get(hit["id"], {})
    claims = ent.get("claims", {})

    def vals(pid):
        out = []
        for c in claims.get(pid, []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v is not None:
                out.append(v)
        return out

    photos = [v for v in vals("P18") if isinstance(v, str)]
    photo_url = (("https://commons.wikimedia.org/wiki/Special:FilePath/"
                  + quote(photos[0]) + "?width=256") if photos else None)
    if not photo_url:   # fall back to the person's Wikipedia lead image
        title = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
        photo_url = _enwiki_image(title)
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
        "photo_url": photo_url,
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
