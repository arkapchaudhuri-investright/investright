# Spec 10 — Notes hub on /account + CSV export

Read `specs/_CONTEXT.md` first. Branch: `feature/notes-hub`. Size: ~2-3h.

## Design
The per-stock journal (`user_notes(user_id, ticker, body, updated_at)`) has no
overview. Add: (a) an "Your notes" section on /account listing every note
(ticker link, first ~140 chars, updated date, newest first); (b) a
`GET /notes.csv` download (login required; a GET that only READS — allowed).

## Step 1 — route additions (app.py or auth.py — put them where /account lives; grep `def account`)
In the account view, load:
```python
notes = conn.execute(
    "SELECT n.ticker, n.body, n.updated_at, s.name FROM user_notes n "
    "JOIN stocks s ON s.ticker = n.ticker WHERE n.user_id=? AND n.body != '' "
    "ORDER BY n.updated_at DESC", (user["id"],)).fetchall()
```
Pass to the template.

New route:
```python
@app.route("/notes.csv")
@login_required
def notes_csv():
    import csv, io
    user = current_user()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ticker", "company", "updated_at", "note"])
    with get_conn() as conn:
        for r in conn.execute(
                "SELECT n.ticker, s.name, n.updated_at, n.body FROM user_notes n "
                "JOIN stocks s ON s.ticker=n.ticker WHERE n.user_id=? "
                "ORDER BY n.updated_at DESC", (user["id"],)):
            w.writerow([r["ticker"], r["name"], r["updated_at"], r["body"]])
    resp = make_response(out.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=investright-notes.csv"
    return resp
```
(If `/account` lives in auth.py's blueprint, put notes_csv there with the
blueprint decorator instead — mirror neighbors.)

## Step 2 — template (account.html)
New section after the existing ones, mirroring their card pattern:
```html
<section class="card" id="notes">
  <div class="card-head"><h2>Your notes</h2>
    {% if notes %}<a class="ghost" href="{{ url_for('notes_csv') }}">Download CSV</a>{% endif %}</div>
  {% if notes %}
  <ul class="notes-hub">
    {% for n in notes %}
    <li>
      <a class="tick" href="{{ url_for('stock', ticker=n['ticker']) }}#notes">{{ n['ticker'] }}</a>
      <span class="notes-hub-body">{{ n['body']|truncate(140, True, '…') }}</span>
      <span class="asof">{{ n['updated_at'][:10] }}</span>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="asof">No notes yet — every stock page has a journal box at the
    bottom; whatever you write shows up here.</p>
  {% endif %}
</section>
```
Also: in stock.html's notes section, show `Last edited {{ note['updated_at'][:10] }}`
as an `.asof` when a note exists (grep `user_note` / `note` in stock.html).

## Step 3 — CSS
```css
.notes-hub { list-style: none; margin: 0; padding: 0; }
.notes-hub li { display: flex; gap: 12px; align-items: baseline; padding: 9px 0;
  border-top: 1px solid var(--hairline); }
.notes-hub li:first-child { border-top: 0; }
.notes-hub .tick { flex: none; text-decoration: none; }
.notes-hub-body { flex: 1; min-width: 0; font-size: 14px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
```

## Tests
- guest GET /notes.csv → 302 to /login (add to test_routes.py).

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client()
r=c.get('/notes.csv'); assert r.status_code in (302,303); print('guard ok')"
```
Authed check via browser or test-client login: /account shows the section;
CSV downloads with the right header row.

## Ship
PR title: `Account: all-notes hub + CSV export`
