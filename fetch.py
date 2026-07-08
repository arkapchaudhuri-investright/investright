"""yfinance wrappers — Yahoo is unofficial, so every call is try/excepted and
callers keep the last-good snapshot when a fetch fails (DESIGN.md §2).
Shared by the web app now and the nightly cron in Phase 2.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf


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
        except Exception:
            pass
        return snap
    except Exception:
        return None


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
