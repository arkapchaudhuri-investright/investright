"""Build static/movers_universe.json — the index constituents the landing
page's movers tiles rank over.

Why a separate, larger universe: the 122 tickers in `stocks` are what people
here actually track, so ranking them and calling the result "market movers"
would be a lie. These are real index members, quote-only — price, day change
and volume, nothing else. Clicking one runs the normal /analyze path, which
ingests deep data on demand, so nothing is fetched for a company nobody opens.

Sources (free, no key, same spirit as build_symbols.py):
  • S&P 500 — Wikipedia's constituents table.
  • NIFTY 50 — Wikipedia's constituents table, suffixed .NS for Yahoo.

Parsed with the stdlib rather than pandas.read_html, which needs lxml — not
worth a dependency for a script that runs by hand every few months.

Run:  .venv/bin/python build_movers.py
"""
import html as html_mod
import json
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (InvestRight build script)"}
OUT = "static/movers_universe.json"

SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NIFTY = "https://en.wikipedia.org/wiki/NIFTY_50"


def _html(url):
    # requests rather than urllib: it ships a CA bundle, and urllib falls back
    # to the system store, which isn't populated on every machine this runs on.
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def _rows(html, table_id):
    """Cells of each <tr> in the table with this id, tags stripped."""
    m = re.search(r'<table[^>]*id="%s".*?</table>' % table_id, html, re.S)
    if not m:
        return []
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        if cells:
            out.append([html_mod.unescape(
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip())
                for c in cells])
    return out


def _column(header, *wanted):
    """Index of the first header cell containing any of `wanted`.

    Header-driven rather than positional: both tables have reordered their
    columns over the years, and a silent off-by-one here would ship sector
    names as company names.
    """
    for i, cell in enumerate(header):
        low = cell.lower()
        if any(w in low for w in wanted):
            return i
    return None


def _members(url, table_id, suffix, market, sym_words, name_words, pattern):
    rows = _rows(_html(url), table_id)
    if not rows:
        return []
    header = rows[0]
    si, ni = _column(header, *sym_words), _column(header, *name_words)
    if si is None or ni is None:
        return []
    out = []
    for cells in rows[1:]:
        if max(si, ni) >= len(cells):
            continue
        sym, name = cells[si].strip(), cells[ni].strip()
        if not re.fullmatch(pattern, sym):
            continue
        out.append([sym.replace(".", "-") + suffix, name, market])
    return out


def sp500():
    # Yahoo writes class shares with a dash where Wikipedia uses a dot.
    return _members(SP500, "constituents", "", "US",
                    ("symbol", "ticker"), ("security", "company"),
                    r"[A-Z][A-Z.\-]{0,6}")


def nifty50():
    return _members(NIFTY, "constituents", ".NS", "IN",
                    ("symbol", "ticker"), ("company", "name"),
                    r"[A-Z&\-]{2,12}")


def main():
    us, ind = [], []
    try:
        us = sp500()
    except Exception as e:
        print(f"  S&P 500 fetch failed: {e.__class__.__name__}: {e}")
    try:
        ind = nifty50()
    except Exception as e:
        print(f"  NIFTY 50 fetch failed: {e.__class__.__name__}: {e}")
    if len(us) < 400 or len(ind) < 40:
        print(f"refusing to write a short list (US {len(us)}, IN {len(ind)}) — "
              "the page layout changed; fix the parser rather than shipping gaps.")
        return 1
    # De-duplicate on symbol, keeping order.
    seen, members = set(), []
    for row in us + ind:
        if row[0] not in seen:
            seen.add(row[0])
            members.append(row)
    json.dump(members, open(OUT, "w"), separators=(",", ":"))
    print(f"wrote {OUT}: {len(members)} members ({len(us)} US, {len(ind)} India)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
