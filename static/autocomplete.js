// Shared client-side autocomplete over the static TICKERS list (loaded from
// tickers.js) — no API per keystroke (DESIGN.md §2). One engine, reused by the
// home hero search and the persistent nav-strip search.
//
// Ranked, typo-tolerant matching over ticker + company name across ALL markets
// (so you can find an Indian stock with the US view selected); anything typed
// still resolves server-side via Yahoo search on submit (names/typos not in
// this list, e.g. "Indian railways").
//
//   IRAutocomplete.attach(inputEl, boxEl[, { onPick }])
//
// boxEl is a <ul> that gets `hidden` toggled and its <li>s filled. onPick(sym)
// fires when a suggestion is chosen (default: submit the input's form).
(function (global) {
  // Search dataset: starts as the small curated TICKERS baked into the page
  // (instant), then grows to the full listed universe (~8k US+NSE symbols) the
  // first time a search field is touched — one static fetch, then everything
  // stays client-side, so there's still no API per keystroke (DESIGN §2).
  var DATA = (typeof TICKERS !== 'undefined') ? TICKERS.slice() : [];
  var fullState = 0;                        // 0 = not loaded, 1 = loading, 2 = done
  function loadFull() {
    if (fullState) return;
    fullState = 1;
    fetch('/static/symbols.json')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rows) {
        if (rows && rows.length) {
          var seen = {};
          DATA.forEach(function (t) { seen[t[0]] = 1; });
          rows.forEach(function (t) { if (!seen[t[0]]) { seen[t[0]] = 1; DATA.push(t); } });
        }
        fullState = 2;
      })
      .catch(function () { fullState = 0; });   // let a later focus retry
  }

  function norm(s) { return s.toLowerCase().replace(/[^a-z0-9 ]/g, ''); }
  function subseq(q, t) {   // are all of q's chars in t, in order? (cheap fuzzy)
    var i = 0;
    for (var j = 0; j < t.length && i < q.length; j++) if (t[j] === q[i]) i++;
    return i === q.length;
  }
  function score(q, sym, name) {
    var s = norm(sym), n = norm(name), qn = norm(q), qc = qn.replace(/ /g, '');
    if (!qc) return 0;
    if (s === qc) return 100;
    if (s.startsWith(qc)) return 90;
    if (qn.split(' ').every(function (w) { return w && n.includes(w); })) return 80;
    if (n.includes(qn)) return 70;
    if (subseq(qc, s)) return 55;                       // typo in ticker
    if (subseq(qc, n.replace(/ /g, ''))) return 45;     // typo in name
    return 0;
  }
  function esc(s) {   // suggestions come from our own static list, but stay safe
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Bold the part of a label the query matched (first occurrence only), so the
  // eye lands on why a row is there. Escapes before wrapping.
  function mark(label, q) {
    var i = label.toLowerCase().indexOf(q.toLowerCase());
    if (!q || i < 0) return esc(label);
    return esc(label.slice(0, i)) + '<mark>' + esc(label.slice(i, i + q.length)) +
      '</mark>' + esc(label.slice(i + q.length));
  }
  var uid = 0;

  function attach(input, box, opts) {
    opts = opts || {};
    var onPick = opts.onPick || function () {
      if (!input.form) return;
      // requestSubmit() fires the native submit event (so the loading overlay
      // shows) and still navigates; .submit() would bypass the event.
      if (input.form.requestSubmit) input.form.requestSubmit();
      else input.form.submit();
    };
    var sel = -1;
    // Combobox semantics, so a screen reader announces the list and the row
    // the arrow keys are on (aria-activedescendant), not just a silent popup.
    if (!box.id) box.id = 'ac-list-' + (++uid);
    box.setAttribute('role', 'listbox');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', box.id);
    input.setAttribute('aria-expanded', 'false');
    function show(on) {
      box.hidden = !on;
      input.setAttribute('aria-expanded', String(on));
      if (!on) input.removeAttribute('aria-activedescendant');
      // A container (the nav sheet) can react to "results are showing".
      if (box.parentElement) box.parentElement.classList.toggle('has-results', on);
    }

    // Warm the full universe as soon as the field is engaged.
    input.addEventListener('focus', loadFull);

    input.addEventListener('input', function () {
      loadFull();
      var q = input.value.trim();
      sel = -1;
      if (q.length < 1 || !DATA.length) { box.innerHTML = ''; show(false); return; }
      var hits = DATA.map(function (t) { return [score(q, t[0], t[1]), t]; })
        .filter(function (p) { return p[0] > 0; })
        .sort(function (a, b) { return b[0] - a[0]; })
        .slice(0, 8).map(function (p) { return p[1]; });
      if (!hits.length) {
        // Nothing in the static list — but the server still resolves names
        // and typos through Yahoo, so say so instead of showing nothing.
        box.innerHTML = '<li class="s-empty" role="presentation">No match in our list for “' +
          esc(q) + '”. Press Return to look it up anyway.</li>';
        show(true);
        return;
      }
      box.innerHTML = hits.map(function (t, i) {
        return '<li role="option" aria-selected="false" id="' + box.id + '-' + i +
          '" data-s="' + esc(t[0]) + '">' +
          '<span class="s-av" aria-hidden="true">' + esc((t[1] || t[0]).charAt(0)) + '</span>' +
          '<span class="s-main"><span class="s-name">' + mark(t[1] || t[0], q) + '</span>' +
          '<span class="s-meta"><b>' + mark(t[0], q) + '</b>' +
          '<span class="xchg">' + esc(t[2]) + '</span></span></span>' +
          '<svg class="s-go" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" ' +
          'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M9 6l6 6-6 6"/></svg></li>';
      }).join('');
      show(true);
    });

    box.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li[data-s]');
      if (li) { input.value = li.dataset.s; show(false); onPick(li.dataset.s); }
    });

    input.addEventListener('keydown', function (e) {
      var items = box.querySelectorAll('li[data-s]');
      if (e.key === 'Escape' && !box.hidden) { e.stopPropagation(); show(false); return; }
      if (box.hidden || !items.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach(function (li, i) {
          li.classList.toggle('active', i === sel);
          li.setAttribute('aria-selected', String(i === sel));
        });
        input.setAttribute('aria-activedescendant', items[sel].id);
        items[sel].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && sel >= 0) {
        input.value = items[sel].dataset.s; show(false);
      }
    });

    input.addEventListener('blur', function () { setTimeout(function () { show(false); }, 150); });
  }

  global.IRAutocomplete = { attach: attach };
})(window);
