"""Portfolio bulk-import parsers (spec 15) — pure, no network, never raise.

Three input shapes normalise to the same list of
``{"raw_symbol", "isin", "qty", "avg_price", "derived_avg"}`` dicts, which the
app then resolves to real
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
    # Zerodha's Console web table and its downloadable holdings workbook use
    # different headers; the workbook wins here since that's what people export.
    # A miss falls through to the keyword guesser, which catches the other one.
    "zerodha":   {"symbol": "Symbol", "qty": "Quantity Available",
                  "price": "Average Price"},
    "groww":     {"symbol": "Stock Name", "qty": "Quantity", "price": "Avg. buy price"},
    "robinhood": {"symbol": "Symbol", "qty": "Quantity", "price": "Average Cost"},
    "fidelity":  {"symbol": "Symbol", "qty": "Quantity", "price": "Cost Basis Per Share"},
    # Schwab's positions export has no per-share average at all — only the
    # position's total "Cost Basis" — so this signature names `cost` and the
    # parser divides by quantity. Its "Price" column is the LIVE price; reading
    # that as the average is what left four US holdings showing $0.00 P&L.
    "schwab":    {"symbol": "Symbol", "qty": "Quantity", "cost": "Cost Basis"},
}

# Header keywords for the generic guesser, most-specific first — and the order
# matters, because the guesser tries KEYWORDS in order rather than walking the
# columns. A Schwab export puts its live "Price" column well to the left of any
# cost figure, so column-order-wins picked the live price as the average and
# every position showed a $0.00 gain forever.
_SYMBOL_KEYS = ("symbol", "ticker", "scrip", "instrument", "stock")
_QTY_KEYS = ("qty", "quantity", "shares", "units")
# Per-share averages. Bare "price" is deliberately absent: it means the live
# price at least as often as the buy price, so it only gets a look in via
# _LAST_PRICE_KEYS, after a total cost basis has also failed to turn up.
_PRICE_KEYS = ("cost basis per share", "cost per share", "average price",
               "average cost", "avg. buy", "avg buy", "avg price", "avg cost",
               "buy price", "buy avg", "avg", "average")
# Whole-position cost. Divided by quantity to get the average (Schwab, and any
# export that reports what you paid in total rather than per share).
_COST_KEYS = ("cost basis", "total cost", "cost value", "buy value",
              "purchase value", "invested")
_LAST_PRICE_KEYS = ("price", "rate", "cost")
_ISIN_KEYS = ("isin",)


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
        out.append({"raw_symbol": sym, "isin": "", "qty": _num(nums[0]),
                    "avg_price": _num(nums[1]), "derived_avg": False})
    return out


def _guess_columns(headers):
    """Best-effort {field: index} from CSV headers by keyword. Missing → absent.

    Keywords are tried in order and the first one that matches any column wins,
    so a more telling header beats a nearer one — "Average Cost" over "Price"
    even when Price sits three columns to its left."""
    lowered = [(h or "").strip().lower() for h in headers]

    def find(keys, taken=()):
        for k in keys:
            for i, h in enumerate(lowered):
                if k in h and i not in taken:
                    return i
        return None

    price = find(_PRICE_KEYS)
    # A per-share average and a total cost can both be present (Fidelity ships
    # "Cost Basis Per Share" next to "Cost Basis"); the average wins, and the
    # column it claimed is off the table for the total.
    cost = find(_COST_KEYS, taken=(price,) if price is not None else ())
    if price is None and cost is None:
        price = find(_LAST_PRICE_KEYS)
    return {"symbol": find(_SYMBOL_KEYS), "qty": find(_QTY_KEYS),
            "price": price, "cost": cost, "isin": find(_ISIN_KEYS)}


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


def _looks_like_header(cells):
    """True when a row names a symbol column plus a quantity or price one.

    Broker exports rarely start with their header: Zerodha's puts 22 rows of
    client id, statement title and a summary block above it. Finding the header
    by content rather than assuming row 0 is what makes those files work."""
    guess = _guess_columns(cells)
    return guess.get("symbol") is not None and (
        guess.get("qty") is not None or guess.get("price") is not None)


def _find_header(rows, limit=40):
    """Index of the first row that reads as a header, else 0 (assume row one)."""
    for i, r in enumerate(rows[:limit]):
        if _looks_like_header([(c or "").strip() for c in r]):
            return i
    return 0


def _sheet_rows(data):
    """Every sheet of an .xlsx/.xlsm workbook as (name, rows), best first.

    Brokers split workbooks by asset class — Zerodha ships Equity, Mutual Funds
    and Combined — so pick by content: sheets whose header we can actually find
    come first, ordered by how many rows follow it. Returns [] for anything
    openpyxl can't open, which the caller treats as "not a spreadsheet"."""
    try:
        import openpyxl
    except ImportError:
        return []
    try:
        # NOT read_only: broker workbooks often carry a wrong or missing
        # dimension record, and in read-only mode openpyxl trusts it and yields
        # zero rows. Holdings files are tens of KB, so the full parse is cheap.
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        return []
    out = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in r]
                for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            continue
        h = _find_header(rows)
        scored = _looks_like_header([c.strip() for c in rows[h]])
        out.append((scored, len(rows) - h, ws.title, rows))
    wb.close()
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(name, rows) for _s, _n, name, rows in out]


def _is_xlsx(data):
    """.xlsx/.xlsm are ZIP archives — sniff the magic bytes rather than trusting
    a filename, since browsers report Excel MIME types inconsistently."""
    return isinstance(data, (bytes, bytearray)) and data[:2] == b"PK"


def _to_rows(raw):
    """Uploaded bytes → a table (list of rows), from a spreadsheet or CSV/TSV."""
    if _is_xlsx(raw):
        sheets = _sheet_rows(bytes(raw))
        return sheets[0][1] if sheets else []
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else (raw or "")
    # Sniff the delimiter: some brokers export tab- or semicolon-separated files
    # with a .csv extension, which a comma-only reader turns into one long column.
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        rows = list(csv.reader(io.StringIO(text), dialect))
    except Exception:
        rows = list(csv.reader(io.StringIO(text)))
    return [r for r in rows if any((c or "").strip() for c in r)]


def _parse_csv(raw, kind, colmap=None):
    """A spreadsheet or CSV → rows. `colmap` (from the confirm-page mapper)
    wins; else a broker signature (when `kind` names one); else keyword
    guessing. The header is located by content, not assumed to be row one."""
    try:
        rows = _to_rows(raw)
    except Exception:
        return []
    if not rows:
        return []
    hrow = _find_header(rows)
    headers = [(h or "").strip() for h in rows[hrow]]

    idx = None
    if colmap:                            # explicit header-name map from the UI
        idx = {}
        for field, header in (("symbol", colmap.get("symbol")),
                              ("qty", colmap.get("qty")),
                              ("price", colmap.get("price")),
                              ("cost", colmap.get("cost"))):
            idx[field] = headers.index(header) if header in headers else None
    elif kind in BROKER_COLS:
        idx = _columns_from_signature(headers, BROKER_COLS[kind])
    if not idx or idx.get("symbol") is None:
        idx = _guess_columns(headers)

    si, qi, pi = idx.get("symbol"), idx.get("qty"), idx.get("price")
    # A broker signature may name a total cost instead of a per-share average
    # (Schwab); an explicit map may name neither, in which case fall back to
    # whatever the keyword guesser found for this file.
    ci = idx.get("cost")
    if pi is None and ci is None:
        guessed = _guess_columns(headers)
        pi, ci = guessed.get("price"), guessed.get("cost")
    # A broker signature or an explicit column map only names the three fields
    # we ask for, so look the ISIN up separately — it's optional, and it's the
    # difference between resolving BEL to Bharat Electronics and to something
    # unrelated that happens to fuzzy-match.
    ii = idx.get("isin")
    if ii is None:
        ii = _guess_columns(headers).get("isin")
    out = []
    for r in rows[hrow + 1:]:
        def cell(i):
            return r[i] if i is not None and i < len(r) else None
        sym = _symbol(cell(si)) if si is not None else ""
        if not sym:
            continue
        qty = _num(cell(qi))
        avg = _num(cell(pi))
        # No per-share figure, but a whole-position cost and a quantity → the
        # average is arithmetic, not a guess. Flagged so the confirm screen can
        # say where the number came from.
        derived = False
        if avg is None and ci is not None and qty:
            total = _num(cell(ci))
            if total is not None:
                avg = total / qty
                derived = True
        out.append({"raw_symbol": sym, "isin": (cell(ii) or "").strip().upper(),
                    "qty": qty, "avg_price": avg, "derived_avg": derived})
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
