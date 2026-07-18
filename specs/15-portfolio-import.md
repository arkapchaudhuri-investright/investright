# Spec 15 — Portfolio bulk import (paste · CSV · broker auto-detect)

Read `specs/_CONTEXT.md` first. Branch: `feature/portfolio-import`.
Size: ~weekend. DEPENDS ON spec 14 (the `holdings` table + `/portfolio`).

## Design
Three ways to load many holdings at once, all $0 and vanilla: paste a table,
upload a generic CSV with a column-mapper, or upload a known broker's export and
have the columns auto-detected. Broker symbols ≠ Yahoo symbols (especially
India), so every path ends at a **confirm screen** where unmatched rows are
resolved by hand — never silently guessed. Nothing is written until the user
confirms.

## Step 1 — the shared confirm flow (the heart of this spec)
All three inputs normalise to a list of `{raw_symbol, qty, avg_price}` then run
through one matcher + confirm page:
- `portfolio_import.parse_rows(text_or_csv, kind)` → list of dicts (pure, tested).
- For each row, resolve `raw_symbol` → a real ticker via `fetch.search()` /
  `fetch.lookup()` (reuse the app's existing resolver). Auto-match on an exact/
  unique hit; otherwise mark `needs_confirm` with the top candidates.
- Render `templates/portfolio_import_confirm.html`: a table of parsed rows, each
  with a ticker (pre-filled or a small select of candidates + a free text box),
  qty, avg price — all editable. Bad/blank rows are flagged, not dropped silently.
- Stash the parsed batch in the session (or a hidden JSON field) so the confirm
  POST doesn't re-upload. **No DB writes until confirm.**

## Step 2 — routes (app.py)
- `POST /portfolio/import` (`@login_required`): accepts either `paste` (textarea)
  or an uploaded `file` (CSV) + a `broker` select (`generic` | `zerodha` |
  `groww` | `robinhood` | `fidelity`). Parse → resolve → render the confirm page.
  Reject empty/oversized uploads (cap ~1 MB, honest flash).
- `POST /portfolio/import/confirm` (`@login_required`): read the edited rows,
  `_ingest_stock` any new tickers, upsert each into `holdings` (same upsert as
  spec 14's add). Flash "Added N holdings (M skipped)". Redirect `/portfolio`.
- CSRF on both.

## Step 3 — parsers (`portfolio_import.py`, pure, no network)
```python
def parse_rows(raw, kind):
    """raw text or CSV bytes → [{'raw_symbol','qty','avg_price'}]; never raises."""
```
- **paste**: split lines; per line pull symbol + two numbers (qty, price) in that
  order; tolerate commas/₹/$/whitespace. Skip header-ish / blank lines.
- **generic CSV**: parse with `csv`; if the POST carried a column map
  (`col_symbol`, `col_qty`, `col_price` = header names), use it; else best-effort
  guess by header keywords (`symbol|ticker|scrip`, `qty|quantity|shares`,
  `avg|price|cost`). Unmapped → the confirm page's mapper.
- **broker auto-detect**: header signatures per broker →
  ```python
  BROKER_COLS = {
    "zerodha":   {"symbol": "Instrument", "qty": "Qty.", "price": "Avg. cost"},
    "groww":     {"symbol": "Stock Name", "qty": "Quantity", "price": "Avg. buy price"},
    "robinhood": {"symbol": "Symbol", "qty": "Quantity", "price": "Average Cost"},
    "fidelity":  {"symbol": "Symbol", "qty": "Quantity", "price": "Cost Basis Per Share"},
  }
  ```
  (Header strings are best-effort — brokers change them; a miss just falls back to
  the generic mapper, never an error. Note this in a comment.)
- India note: Zerodha/Groww give NSE symbols without `.NS`; the resolver adds the
  suffix via `fetch.search`. Ambiguous → confirm page.

## Step 4 — UI
- On `/portfolio` (spec 14), an "Import holdings" `<details>` with: a paste
  textarea, OR a file input + broker select. Vanilla, progressive — the confirm
  page is a normal server-rendered form.
- Confirm page: clear per-row status (✓ matched / ⚑ needs a ticker / ✕ bad
  numbers), a Confirm button, and a Cancel link. Honest counts.

## Step 5 — tests (tests/test_portfolio_import.py)
- `parse_rows` paste: mixed good/blank/garbage lines → correct dicts, no raise.
- `parse_rows` generic CSV with a column map → correct dicts.
- broker CSV (a Zerodha-shaped header fixture) → correct dicts.
- currency/symbol junk (₹, commas) stripped.
- guest `POST /portfolio/import` → 302 login.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import portfolio_import as p
print(p.parse_rows('AAPL 10 150\nMSFT, 5, 300\n\nnope', 'paste'))"
```
Then via test client with dev login: paste two rows → confirm page lists both →
confirm → /portfolio shows them. Upload a small Zerodha-shaped CSV → India
tickers resolve (or land on confirm) → confirm → holdings added.

## Ship
PR title: `Portfolio import: paste, generic CSV + broker auto-detect with confirm`
