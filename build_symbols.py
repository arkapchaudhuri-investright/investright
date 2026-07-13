"""Build static/symbols.json — the full search universe for autocomplete.

Fetches the listed universe from free sources and writes a compact
[[symbol, name, exchange], ...] JSON the client lazy-loads on first search
focus (see static/autocomplete.js). Data is pulled live only when a user opens
a stock, so this is purely the *discovery* list — no per-stock fetching here.

Sources (all free, no key):
  • US (NASDAQ / NYSE / AMEX): the rreichel3/US-Stock-Symbols mirror of the
    official Nasdaq Trader symbol directory (daily-updated JSON with names +
    market cap). Nasdaq Trader's own FTP is the upstream but is often blocked.
  • India (NSE): the official EQUITY_L.csv equity list.

Filtering (per the product decision): real common shares only — warrants,
units, rights, preferreds and notes are dropped; ADRs/depositary shares are
kept (they're the primary US listing for many foreign companies). India is
NSE-only (clean .NS symbols; BSE's numeric .BO codes duplicate the same firms).

Run:  .venv/bin/python build_symbols.py   (writes static/symbols.json)
"""
import csv
import io
import json
import re
from pathlib import Path

import requests

OUT = Path(__file__).parent / "static" / "symbols.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; InvestRight/1.0 symbol builder)"}
US_MIRROR = ("https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/"
             "main/{ex}/{ex}_full_tickers.json")
NSE_CSV = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# Name patterns that mark a non-common security. ADR / depositary deliberately
# absent — those are real, searchable listings (Alibaba, TSMC, …).
_JUNK = re.compile(
    r"\b(warrants?|units?|rights?|preferred|preference|cumulative|redeemable|"
    r"senior notes?|subordinated notes?|debentures?|when[- ]issued|"
    r"depositary units|test)\b", re.I)
# A percentage in the name almost always means a preferred/note coupon.
_RATE = re.compile(r"\d\s*%")
# Trailing boilerplate to trim so autocomplete reads cleanly.
_BOILER = re.compile(
    r"\s*(,?\s*(inc\.?|corp\.?|corporation|ltd\.?|limited|plc|co\.?|company|"
    r"holdings?|group))?\s*(common stock|common shares|ordinary shares|"
    r"american depositary (shares|receipts)|class [a-z] (common stock|"
    r"ordinary shares)).*$", re.I)


def _clean_name(name):
    name = re.sub(r"\s*\([^)]*\)", "", name or "").strip()   # drop parentheticals
    trimmed = _BOILER.sub("", name).strip(" ,.-")
    return trimmed or name                                    # never blank


def _yahoo_symbol(sym):
    """Mirror uses '/' (and rarely '.') for class shares; Yahoo wants '-'."""
    return sym.replace("/", "-").replace(".", "-").strip().upper()


def _fetch_us():
    out = []
    for ex, label in (("nasdaq", "NASDAQ"), ("nyse", "NYSE"), ("amex", "NYSE American")):
        rows = requests.get(US_MIRROR.format(ex=ex), headers=UA, timeout=45).json()
        for r in rows:
            sym, name = (r.get("symbol") or "").strip(), (r.get("name") or "").strip()
            if not sym or not name:
                continue
            if "^" in sym or "$" in sym:                     # preferred / warrant series
                continue
            tail = re.split(r"[/.]", sym)[-1]
            if tail in ("W", "WS", "WT", "U", "UN", "R", "RT"):  # unit/warrant/right
                continue
            if _JUNK.search(name) or _RATE.search(name):
                continue
            try:
                mc = float(r.get("marketCap") or 0)
            except (TypeError, ValueError):
                mc = 0.0
            out.append((_yahoo_symbol(sym), _clean_name(name), label, mc))
    return out


def _fetch_nse():
    text = requests.get(NSE_CSV, headers={**UA, "Accept": "text/csv,*/*"},
                        timeout=45).text
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        series = (row.get(" SERIES") or row.get("SERIES") or "").strip()
        if series and series != "EQ":                        # main equity series only
            continue
        sym = (row.get("SYMBOL") or "").strip().upper()
        name = (row.get("NAME OF COMPANY") or "").strip()
        if sym and name:
            out.append((sym + ".NS", _clean_name(name), "NSE", 0.0))
    return out


def main():
    us = _fetch_us()
    nse = _fetch_nse()
    # Largest US caps first (a tiebreak when scores tie in autocomplete), then
    # NSE alphabetically. Dedup by final Yahoo symbol, US winning over NSE.
    us.sort(key=lambda t: -t[3])
    nse.sort(key=lambda t: t[1].lower())
    seen, merged = set(), []
    for sym, name, exch, _mc in us + nse:
        if sym in seen:
            continue
        seen.add(sym)
        merged.append([sym, name, exch])
    OUT.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")))
    kb = OUT.stat().st_size / 1024
    print(f"wrote {len(merged)} symbols → {OUT} ({kb:.0f} KB)")
    print(f"  US: {len(us)}  ·  NSE: {len(nse)}  ·  after dedup: {len(merged)}")


if __name__ == "__main__":
    main()
