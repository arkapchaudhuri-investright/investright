"""yfinance wrappers — Yahoo is unofficial, so every call is try/excepted and
callers keep the last-good snapshot when a fetch fails (DESIGN.md §2).
Shared by the web app now and the nightly cron in Phase 2.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yfinance as yf


# Major, liquid exchanges — a hit here beats an obscure foreign cross-listing
# (e.g. AAPL on NASDAQ over "AAPL19.BK" in Bangkok).
_MAJOR_EXCH = {"NMS", "NGM", "NCM", "NYQ", "PCX", "ASE",  # US
               "NSI", "BSE",                              # India
               "LSE", "TOR"}


def search(query, limit=10):
    """Resolve free text — a company name, a typo, 'reliance industries' — to
    the best-matching ticker via Yahoo's search endpoint. Free, no key. Returns
    an uppercased symbol or None. Ranks equities on major exchanges first, and
    leans Indian (.NS/.BO) when the query mentions India."""
    q = (query or "").strip()
    if not q:
        return None
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": limit, "newsCount": 0,
                    "enableFuzzyQuery": "true"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        r.raise_for_status()
        quotes = r.json().get("quotes", [])
    except Exception:
        return None
    india_hint = "india" in q.lower()

    def rank(idx, quote):
        sym = (quote.get("symbol") or "").upper()
        score = 0
        if quote.get("quoteType") == "EQUITY":
            score += 100
        elif quote.get("quoteType") in ("ETF", "MUTUALFUND", "INDEX"):
            score += 40
        if quote.get("exchange") in _MAJOR_EXCH:
            score += 30
        if india_hint and (sym.endswith(".NS") or sym.endswith(".BO")):
            score += 25
        score -= idx                      # keep Yahoo's own relevance as a tiebreak
        return score

    ranked = sorted(
        (quote for quote in quotes if quote.get("symbol")),
        key=lambda pair: rank(quotes.index(pair), pair), reverse=True)
    return ranked[0]["symbol"].upper() if ranked else None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lookup(symbol):
    """Metadata for a new ticker, or None if Yahoo doesn't recognise it."""
    try:
        info = yf.Ticker(symbol).info or {}
        name = info.get("shortName") or info.get("longName")
        if not name or info.get("regularMarketPrice") is None:
            return None
        return {
            "ticker": symbol.upper(),
            "name": name,
            "exchange": info.get("fullExchangeName") or info.get("exchange") or "",
            "sector": info.get("sector") or "",
            "currency": info.get("currency") or "USD",
            "website": info.get("website") or "",   # for the company logo (Phase 9)
        }
    except Exception:
        return None


def snapshot(symbol):
    """Current quote + valuation basics, or None on any failure."""
    try:
        t = yf.Ticker(symbol)
        fi = t.fast_info
        price, prev = fi.last_price, fi.previous_close
        if not price or not prev:
            return None
        snap = {
            "ticker": symbol.upper(),
            "fetched_at": _now(),
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change_pct": round((price - prev) / prev * 100, 2),
            "market_cap": fi.market_cap,
            "pe": None,
            "div_yield": None,
            "wk52_low": fi.year_low,
            "wk52_high": fi.year_high,
            "pb": None,
            "ps": None,
            "eps": None,
            "industry_pe": None,   # no free source yet → value check stays n/a
        }
        try:  # .info is slower and flakier — quote still counts without it
            info = t.info or {}
            snap["pe"] = info.get("trailingPE")
            snap["pb"] = info.get("priceToBook")
            snap["ps"] = info.get("priceToSalesTrailing12Months")
            snap["eps"] = info.get("trailingEps")
            # dividendYield units changed across yfinance versions; rate/price is unambiguous
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
            if rate:
                snap["div_yield"] = round(rate / price * 100, 2)
            # Analyst consensus (sentiment widget): Yahoo's aggregated ratings.
            snap["rec_key"] = info.get("recommendationKey")
            snap["rec_mean"] = info.get("recommendationMean")   # 1 strong buy … 5 sell
            snap["analyst_n"] = info.get("numberOfAnalystOpinions")
            snap["target_mean"] = info.get("targetMeanPrice")
        except Exception:
            pass
        return snap
    except Exception:
        return None


def price_history(symbol, period="max"):
    """Daily closes as [(YYYY-MM-DD, close)], oldest first; [] on failure.
    Feeds the deep-dive trend chart — full history at ingest, a short top-up
    nightly (see refresh.py)."""
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d",
                                         auto_adjust=True)
        if hist is None or hist.empty:
            return []
        closes = hist["Close"].dropna()
        return [(idx.strftime("%Y-%m-%d"), round(float(c), 4))
                for idx, c in closes.items()]
    except Exception:
        return []


def _sibling_symbol(symbol):
    """The other-exchange twin for an Indian listing: BSE (.BO) ↔ NSE (.NS)."""
    if symbol.endswith(".BO"):
        return symbol[:-3] + ".NS"
    if symbol.endswith(".NS"):
        return symbol[:-3] + ".BO"
    return None


def price_history_resilient(symbol, period="max", min_rows=2):
    """price_history(), but resilient to Yahoo's near-empty BSE (.BO) series.

    Yahoo often returns just today's close for a `.BO` ticker while its NSE
    (`.NS`) twin has years of data (e.g. IRFC.BO → 1 row, IRFC.NS → 1,348). The
    two track to the paisa, so for the trend chart they're interchangeable. If
    the requested symbol comes back thin, fall back to the sibling exchange and
    return whichever series is richer — so a first-time search always charts."""
    rows = price_history(symbol, period)
    if len(rows) >= min_rows:
        return rows
    sib = _sibling_symbol(symbol)
    if sib:
        alt = price_history(sib, period)
        if len(alt) > len(rows):
            return alt
    return rows


def intraday(symbol):
    """Today's 5-minute closes as [(HH:MM, close)] — the 1D trend tab. Fetched
    live per request (read-only; never written to the DB)."""
    try:
        hist = yf.Ticker(symbol).history(period="1d", interval="5m")
        if hist is None or hist.empty:
            return []
        closes = hist["Close"].dropna()
        return [(idx.strftime("%H:%M"), round(float(c), 4))
                for idx, c in closes.items()]
    except Exception:
        return []


def fx_rate(pair="USDINR=X"):
    """Latest FX rate from Yahoo, or None on failure (caller keeps last-good)."""
    try:
        rate = yf.Ticker(pair).fast_info.last_price
        return round(rate, 4) if rate else None
    except Exception:
        return None


def snapshot_many(symbols):
    """Fetch snapshots concurrently; silently drops failures (last-good wins)."""
    if not symbols:
        return []
    with ThreadPoolExecutor(max_workers=8) as ex:
        return [s for s in ex.map(snapshot, symbols) if s]


# --- deep fundamentals + ratios + news (Phase 3, §8.1) ---------------------
# yfinance line-item aliases (Yahoo occasionally renames rows across tickers).
_INCOME = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "ebit": ("EBIT", "Operating Income"),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "shares": ("Diluted Average Shares", "Basic Average Shares"),
}
_BALANCE = {
    "total_assets": ("Total Assets",),
    "total_liab": ("Total Liabilities Net Minority Interest",),
    "current_assets": ("Current Assets",),
    "current_liab": ("Current Liabilities",),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "total_debt": ("Total Debt",),
    "equity": ("Stockholders Equity", "Common Stock Equity"),
}
_CASHFLOW = {
    "fcf": ("Free Cash Flow",),
    "op_cash_flow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "capex": ("Capital Expenditure",),
    "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
}


# Income-statement line items for the Revenue & Expenses widget (latest year).
_INCOME_FLOW = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "cost_of_rev": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
    "gross_profit": ("Gross Profit",),
    "rd": ("Research And Development",),
    "sga": ("Selling General And Administration",
            "Selling General And Administrative Expense"),
    "operating_inc": ("Operating Income", "EBIT"),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "tax": ("Tax Provision",),
}


def _flow_from_col(df, col):
    """One period's income-flow dict from statement column `col`, or None.
    Fills gross_profit / cost_of_rev from each other when one is missing."""
    out = {k: _pick(df, al, col) for k, al in _INCOME_FLOW.items()}
    if out.get("revenue") is None:
        return None
    if out.get("gross_profit") is None and out.get("cost_of_rev") is not None:
        out["gross_profit"] = out["revenue"] - out["cost_of_rev"]
    elif out.get("cost_of_rev") is None and out.get("gross_profit") is not None:
        out["cost_of_rev"] = out["revenue"] - out["gross_profit"]
    return out


def income_breakdowns(symbol):
    """Every income-statement period Yahoo serves for the Revenue & Expenses
    widget: ~5 annual years + the recent ~5 quarters ("last 5 years or max
    whatever is there"), in the stock's native currency. [] on failure.

    Each row: period ("FY2025" | "2026Q1"), ptype ("A" | "Q"), end_date, and
    the line items. Quarter numbering is calendar (end-month // 3) — a display
    label, not a fiscal-quarter claim; Y/Y matching uses end_date."""
    try:
        t = yf.Ticker(symbol)
        annual, quarterly = t.income_stmt, t.quarterly_income_stmt
    except Exception:
        return []
    rows = []
    for df, ptype in ((annual, "A"), (quarterly, "Q")):
        for col in getattr(df, "columns", []):
            try:
                end = col.date().isoformat()
            except AttributeError:
                continue
            flow = _flow_from_col(df, col)
            if not flow:
                continue
            if ptype == "A":
                period = f"FY{col.year}"
            else:
                period = f"{col.year}Q{(col.month + 2) // 3}"
            rows.append({**flow, "period": period, "ptype": ptype,
                         "end_date": end})
    return rows


def _pick(df, aliases, col):
    """First non-NaN value among `aliases` rows for statement column `col`."""
    if df is None or col not in getattr(df, "columns", []):
        return None
    for row in aliases:
        if row in df.index:
            v = df.at[row, col]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return float(v)
    return None


def _by_year(df):
    """Map fiscal year → statement column (latest wins on ties)."""
    out = {}
    for col in getattr(df, "columns", []):
        try:
            out[col.year] = col
        except AttributeError:
            pass
    return out


def deep(symbol):
    """Everything the deep-dive needs from Yahoo, each part independently guarded.

    Returns {"fundamentals": [...oldest→newest], "ratios": {...}, "news": [...]}.
    Any piece Yahoo won't serve comes back empty rather than raising — the page
    still renders from whatever we have / last-good in the DB (§8.0).
    """
    try:
        t = yf.Ticker(symbol)
    except Exception:
        return {"fundamentals": [], "ratios": {}, "news": []}

    inc = bal = cf = None
    try:
        inc, bal, cf = t.financials, t.balance_sheet, t.cashflow
    except Exception:
        pass

    fundamentals = []
    try:
        years = sorted(set(_by_year(inc)) | set(_by_year(bal)) | set(_by_year(cf)))
        yi, yb, yc = _by_year(inc), _by_year(bal), _by_year(cf)
        for yr in years:
            row = {"fiscal_year": yr}
            for k, al in _INCOME.items():
                row[k] = _pick(inc, al, yi.get(yr))
            for k, al in _BALANCE.items():
                row[k] = _pick(bal, al, yb.get(yr))
            for k, al in _CASHFLOW.items():
                row[k] = _pick(cf, al, yc.get(yr))
            if row.get("fcf") is None and row.get("op_cash_flow") is not None \
                    and row.get("capex") is not None:
                row["fcf"] = row["op_cash_flow"] + row["capex"]  # capex is negative
            if row.get("dividends_paid") is not None:
                row["dividends_paid"] = abs(row["dividends_paid"])
            # keep a year only if it carried at least one real figure
            if any(row.get(k) is not None for k in
                   ("revenue", "net_income", "fcf", "total_assets")):
                fundamentals.append(row)
    except Exception:
        fundamentals = []

    ratios = {}
    try:
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price is None:
            try:
                price = t.fast_info.last_price
            except Exception:
                price = None
        ratios = {
            "price": price,
            "shares": info.get("sharesOutstanding"),
            "pe": info.get("trailingPE"),
            "pb": info.get("priceToBook"),
            "ps": info.get("priceToSalesTrailing12Months"),
            "eps": info.get("trailingEps"),
            "div_yield": (round(info["dividendRate"] / price * 100, 2)
                          if info.get("dividendRate") and price else None),
            "payout_ratio": info.get("payoutRatio"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "industry_pe": None,   # no free source
            "website": info.get("website") or "",   # for the company logo (Phase 9)
        }
    except Exception:
        ratios = {}

    news = []
    try:
        for n in (t.news or [])[:10]:
            c = n.get("content", n)
            url = (c.get("canonicalUrl") or {}).get("url") \
                or (c.get("clickThroughUrl") or {}).get("url") or c.get("link")
            if not url:
                continue
            prov = c.get("provider") or {}
            news.append({
                "title": c.get("title") or "(untitled)",
                "url": url,
                "publisher": prov.get("displayName") or c.get("publisher") or "",
                "published_at": c.get("pubDate") or c.get("providerPublishTime"),
            })
    except Exception:
        news = []

    return {"fundamentals": fundamentals, "ratios": ratios, "news": news}
