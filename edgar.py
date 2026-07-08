"""SEC EDGAR `companyfacts` fetcher — US tickers only (DESIGN.md §8.1 / §8.5 Tier B).

No API key; SEC just requires an identifying User-Agent. Every network call is
try/excepted so a down/rate-limited SEC never breaks refresh.py — callers fall
back to the yfinance path on any failure (§8.0). Not used for India (.NS/.BO);
EDGAR has no equivalent there (§1).
"""
import json
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
TICKER_MAP_PATH = DATA_DIR / "edgar_tickers.json"
UA = "InvestRight personal research project (contact: arkap.chaudhuri@gmail.com)"
HEADERS = {"User-Agent": UA}
MAX_YEARS = 10

# us-gaap concept aliases (most-preferred first — tags get renamed across years,
# e.g. ASC 606 revenue recognition) → (is_duration_fact, unit_key).
_CONCEPTS = {
    "revenue": (("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "SalesRevenueNet", "SalesRevenueGoodsNet"), True, "USD"),
    "net_income": (("NetIncomeLoss", "ProfitLoss"), True, "USD"),
    "ebit": (("OperatingIncomeLoss",), True, "USD"),
    "interest_expense": (("InterestExpense", "InterestExpenseDebt",
                          "InterestExpenseNonoperating"), True, "USD"),
    "op_cash_flow": (("NetCashProvidedByUsedInOperatingActivities",
                      "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), True, "USD"),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndOther"), True, "USD"),
    "dividends_paid": (("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"), True, "USD"),
    "shares": (("WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"), True, "shares"),
    "total_assets": (("Assets",), False, "USD"),
    "total_liab": (("Liabilities",), False, "USD"),
    "current_assets": (("AssetsCurrent",), False, "USD"),
    "current_liab": (("LiabilitiesCurrent",), False, "USD"),
    "long_term_debt": (("LongTermDebtNoncurrent", "LongTermDebt"), False, "USD"),
    "current_debt": (("DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"), False, "USD"),
    "equity": (("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), False, "USD"),
}


def _get_json(url, timeout=15):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _load_ticker_map():
    """ticker → CIK, from SEC's static list; cached and refetched at most weekly."""
    try:
        age = time.time() - TICKER_MAP_PATH.stat().st_mtime
        if age < 7 * 24 * 3600:
            return json.loads(TICKER_MAP_PATH.read_text())
    except Exception:
        pass
    try:
        raw = _get_json("https://www.sec.gov/files/company_tickers.json")
        mapping = {row["ticker"].upper(): row["cik_str"] for row in raw.values()}
        DATA_DIR.mkdir(exist_ok=True)
        TICKER_MAP_PATH.write_text(json.dumps(mapping))
        return mapping
    except Exception:
        try:
            return json.loads(TICKER_MAP_PATH.read_text())
        except Exception:
            return {}


def cik_for(ticker):
    """10-digit zero-padded CIK string for a US ticker, or None if unknown to SEC."""
    mapping = _load_ticker_map()
    cik = mapping.get(ticker.upper())
    return f"{cik:010d}" if cik else None


def _is_annual_duration(entry):
    try:
        start, end = date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
    except (KeyError, ValueError, TypeError):
        return False
    return 330 <= (end - start).days <= 400


def _annual_series(fact, is_duration, unit_key):
    """{fiscal_year: value} from 10-K annual facts; latest filing wins on repeats.

    SEC's `fy` field tags a fact with the *filing's* fiscal year, not the period
    it describes (prior-year comparatives share the filing's fy) — so periods
    are keyed by the fact's own `end` date instead, and sub-annual figures that
    still carry fp='FY' (seen in some comparative tables) are filtered out by
    duration.
    """
    if not fact:
        return {}
    entries = (fact.get("units") or {}).get(unit_key) or []
    best = {}  # end date -> (filed, val)
    for e in entries:
        if e.get("form") not in ("10-K", "10-K/A") or e.get("val") is None or not e.get("end"):
            continue
        if is_duration and not _is_annual_duration(e):
            continue
        if not is_duration and e.get("start"):  # instant facts have no start
            continue
        filed = e.get("filed", "")
        cur = best.get(e["end"])
        if cur is None or filed > cur[0]:
            best[e["end"]] = (filed, e["val"])
    return {int(end[:4]): v[1] for end, v in best.items()}


def _concept_series(gaap, aliases, is_duration, unit_key):
    """Merge alias tags so a mid-history rename (e.g. revenue under ASC 606)
    doesn't truncate the series — earlier aliases in the tuple win per-year."""
    merged = {}
    for tag in aliases:
        for fy, val in _annual_series(gaap.get(tag), is_duration, unit_key).items():
            merged.setdefault(fy, val)
    return merged


def fundamentals(ticker):
    """Up to MAX_YEARS of annual statement rows from EDGAR, oldest→newest.

    Same row shape as fetch.deep()['fundamentals'] (plus transient capex /
    interest_expense / current_debt used only to derive fcf / total_debt at
    refresh time — mirrors fetch.py's convention). Returns [] on any failure
    or if the ticker isn't in SEC's filer list.
    """
    cik = cik_for(ticker)
    if not cik:
        return []
    try:
        facts = _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        gaap = facts.get("facts", {}).get("us-gaap", {})
    except Exception:
        return []
    if not gaap:
        return []

    series = {key: _concept_series(gaap, aliases, is_dur, unit)
              for key, (aliases, is_dur, unit) in _CONCEPTS.items()}
    years = sorted(set().union(*(s.keys() for s in series.values())))

    rows = []
    for fy in years:
        row = {"fiscal_year": fy}
        for key, s in series.items():
            row[key] = s.get(fy)
        if row.get("op_cash_flow") is not None and row.get("capex") is not None:
            row["fcf"] = row["op_cash_flow"] - row["capex"]  # EDGAR capex is a positive outflow
        else:
            row["fcf"] = None
        ltd, cd = row.get("long_term_debt"), row.get("current_debt")
        row["total_debt"] = (ltd or 0) + (cd or 0) if (ltd is not None or cd is not None) else None
        if any(row.get(k) is not None for k in ("revenue", "net_income", "fcf", "total_assets")):
            rows.append(row)

    return rows[-MAX_YEARS:]


# --- Form 4 insider transactions (Tier C, §4.8 / §8.5) ----------------------
# A different EDGAR surface than companyfacts: the `submissions` filing index
# plus each filing's ownership XML — XBRL facts don't carry insider trades.

FORM4_MAX = 12

_TX_LABEL = {"P": "buy", "S": "sell", "A": "award", "M": "exercise",
             "G": "gift", "F": "tax-withhold", "D": "disposition"}


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _form4_xml_url(cik_int, accession, primary_doc):
    """URL of a filing's raw ownership XML. `primaryDocument` is sometimes the
    xsl-rendered path (`xslF345X05/foo.xml`) — the basename is the raw file.
    Falls back to the filing's index.json when it isn't an .xml at all."""
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession.replace('-', '')}"
    doc = (primary_doc or "").split("/")[-1]
    if doc.endswith(".xml"):
        return f"{base}/{doc}"
    try:
        idx = _get_json(f"{base}/index.json")
        for item in idx.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.endswith(".xml") and not name.startswith("R"):
                return f"{base}/{name}"
    except Exception:
        pass
    return None


def insider_transactions(ticker, max_filings=FORM4_MAX):
    """Recent insider trades from the company's Form 4 filings, newest first.

    One row per (filing, transaction code): same-code lines within a filing are
    summed (option vests arrive in tranches). Returns [] on any failure so
    callers keep last-good rows (§8.0). US only — Form 4 has no India equivalent.
    """
    cik = cik_for(ticker)
    if not cik:
        return []
    try:
        recent = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")["filings"]["recent"]
    except Exception:
        return []
    cik_int = int(cik)

    out, seen = [], 0
    for form, acc, doc, filed in zip(recent.get("form", []), recent.get("accessionNumber", []),
                                     recent.get("primaryDocument", []), recent.get("filingDate", [])):
        if form != "4":
            continue
        seen += 1
        if seen > max_filings:
            break
        url = _form4_xml_url(cik_int, acc, doc)
        if not url:
            continue
        try:
            time.sleep(0.15)  # SEC fair-access: stay well under 10 req/s
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            continue

        name = (root.findtext(".//rptOwnerName") or "").title()
        rel = root.find(".//reportingOwnerRelationship")
        role = ""
        if rel is not None:
            role = (rel.findtext("officerTitle") or "").strip()
            if not role and (rel.findtext("isDirector") or "").strip() in ("1", "true"):
                role = "Director"
            if not role and (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
                role = "10% owner"

        agg = {}  # transaction code -> summed shares/value
        for tx in root.iter("nonDerivativeTransaction"):
            code = (tx.findtext(".//transactionCode") or "?").strip()
            shares = _float(tx.findtext(".//transactionShares/value"))
            price = _float(tx.findtext(".//transactionPricePerShare/value"))
            a = agg.setdefault(code, {"shares": 0.0, "value": 0.0,
                                      "date": tx.findtext(".//transactionDate/value") or filed})
            if shares:
                a["shares"] += shares
                if price:
                    a["value"] += shares * price

        index_url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                     f"{acc.replace('-', '')}/{acc}-index.htm")
        for code, a in agg.items():
            price = (a["value"] / a["shares"]) if a["shares"] and a["value"] else None
            out.append({"filed_at": a["date"], "name": name, "role": role,
                        "action": _TX_LABEL.get(code, "other"), "code": code,
                        "shares": a["shares"] or None,
                        "price": round(price, 2) if price else None,
                        "value": round(a["value"]) if a["value"] else None,
                        "url": index_url})

    out.sort(key=lambda r: r["filed_at"] or "", reverse=True)
    return out
