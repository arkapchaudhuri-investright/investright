"""Portfolio bulk-import parsers (spec 15) — pure, no network, never raise.

Three input shapes normalise to the same list of
``{"raw_symbol", "qty", "avg_price"}`` dicts, which the app then resolves to real
Yahoo tickers and shows on a confirm screen before anything is written:

- **paste**   — a pasted table (one holding per line: symbol + qty + price).
- **generic** — a CSV, columns picked by an explicit map or guessed by header.
- a **broker** name — a CSV whose columns are auto-detected from that broker's
  known header signature (see ``BROKER_COLS``).

Symbol resolution and DB writes live in app.py; this module only turns messy
text into structured rows. Numbers keep ``None`` when unparseable so the confirm
page can flag them (✕ bad) rather than silently dropping a holding.
"""
import csv
import io
import re

# Broker export header signatures. BEST-EFFORT: brokers rename columns over time,
# so a miss here just falls through to the generic keyword guesser — never an
# error. Keys are the exact header text as of writing; values map to our fields.
BROKER_COLS = {
    "zerodha":   {"symbol": "Instrument", "qty": "Qty.", "price": "Avg. cost"},
    "groww":     {"symbol": "Stock Name", "qty": "Quantity", "price": "Avg. buy price"},
    "robinhood": {"symbol": "Symbol", "qty": "Quantity", "price": "Average Cost"},
    "fidelity":  {"symbol": "Symbol", "qty": "Quantity", "price": "Cost Basis Per Share"},
}

# Header keywords for the generic guesser, most-specific first.
_SYMBOL_KEYS = ("symbol", "ticker", "scrip", "instrument", "stock")
_QTY_KEYS = ("qty", "quantity", "shares", "units")
_PRICE_KEYS = ("avg", "average", "price", "cost", "buy")


def _num(s):
    """First number in a messy cell → float, or None. Strips ₹/$/€/£, spaces and
    thousands commas; keeps the decimal point and a leading minus."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _symbol(s):
    """Normalise a raw symbol cell: strip surrounding junk, keep an uppercased
    ticker-ish token (letters/digits/dot/&/-). '' when nothing usable."""
    if not s:
        return ""
    m = re.search(r"[A-Za-z][A-Za-z0-9.&\-]*", str(s))
    return m.group(0).upper() if m else ""


def _parse_paste(raw):
    """One holding per line: a symbol then two numbers (qty, price) in that
    order. Currency symbols and thousands commas are tolerated. Lines without a
    symbol AND two numbers (headers, blanks, notes) are skipped."""
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sym = _symbol(line)
        if not sym:
            continue
        rest = line[line.upper().find(sym) + len(sym):]
        nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", rest)
        if len(nums) < 2:                 # not a holding line — skip quietly
            continue
        out.append({"raw_symbol": sym,
                    "qty": _num(nums[0]), "avg_price": _num(nums[1])})
    return out


def _guess_columns(headers):
    """Best-effort {field: index} from CSV headers by keyword. Missing → absent."""
    lowered = [(h or "").strip().lower() for h in headers]

    def find(keys):
        for i, h in enumerate(lowered):
            if any(k in h for k in keys):
                return i
        return None
    return {"symbol": find(_SYMBOL_KEYS), "qty": find(_QTY_KEYS),
            "price": find(_PRICE_KEYS)}


def _columns_from_signature(headers, sig):
    """Map a broker's header signature to indices; None if any column is absent
    (caller falls back to the generic guesser)."""
    idx = {}
    for field, name in sig.items():
        try:
            idx[field] = headers.index(name)
        except ValueError:
            return None
    return idx


def _parse_csv(raw, kind, colmap=None):
    """CSV text → rows. `colmap` (from the confirm-page mapper) wins; else a
    broker signature (when `kind` names one); else keyword guessing."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else (raw or "")
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return []
    rows = [r for r in rows if any((c or "").strip() for c in r)]  # drop blank lines
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]

    idx = None
    if colmap:                            # explicit header-name map from the UI
        idx = {}
        for field, header in (("symbol", colmap.get("symbol")),
                              ("qty", colmap.get("qty")),
                              ("price", colmap.get("price"))):
            idx[field] = headers.index(header) if header in headers else None
    elif kind in BROKER_COLS:
        idx = _columns_from_signature(headers, BROKER_COLS[kind])
    if not idx or idx.get("symbol") is None:
        idx = _guess_columns(headers)

    si, qi, pi = idx.get("symbol"), idx.get("qty"), idx.get("price")
    out = []
    for r in rows[1:]:
        def cell(i):
            return r[i] if i is not None and i < len(r) else None
        sym = _symbol(cell(si)) if si is not None else ""
        if not sym:
            continue
        out.append({"raw_symbol": sym,
                    "qty": _num(cell(qi)), "avg_price": _num(cell(pi))})
    return out


def parse_rows(raw, kind, colmap=None):
    """Raw text or CSV bytes → [{'raw_symbol','qty','avg_price'}]. Never raises.

    `kind`: "paste" | "generic" | a BROKER_COLS key. `colmap` (optional):
    {"symbol","qty","price"} header names from the confirm-page mapper."""
    try:
        if kind == "paste":
            return _parse_paste(raw)
        return _parse_csv(raw, kind, colmap)
    except Exception:
        return []
