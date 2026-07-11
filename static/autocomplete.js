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

    input.addEventListener('input', function () {
      var q = input.value.trim();
      sel = -1;
      if (q.length < 1 || typeof TICKERS === 'undefined') { box.hidden = true; box.innerHTML = ''; return; }
      var hits = TICKERS.map(function (t) { return [score(q, t[0], t[1]), t]; })
        .filter(function (p) { return p[0] > 0; })
        .sort(function (a, b) { return b[0] - a[0]; })
        .slice(0, 8).map(function (p) { return p[1]; });
      box.innerHTML = hits.map(function (t) {
        return '<li data-s="' + esc(t[0]) + '"><b>' + esc(t[0]) + '</b>' +
          '<span class="xchg">' + esc(t[2]) + '</span>' +
          '<span class="s-name">' + esc(t[1]) + '</span></li>';
      }).join('');
      box.hidden = hits.length === 0;
    });

    box.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li');
      if (li) { input.value = li.dataset.s; box.hidden = true; onPick(li.dataset.s); }
    });

    input.addEventListener('keydown', function (e) {
      var items = box.querySelectorAll('li');
      if (box.hidden || !items.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach(function (li, i) { li.classList.toggle('active', i === sel); });
      } else if (e.key === 'Enter' && sel >= 0) {
        input.value = items[sel].dataset.s; box.hidden = true;
      } else if (e.key === 'Escape') {
        box.hidden = true;
      }
    });

    input.addEventListener('blur', function () { setTimeout(function () { box.hidden = true; }, 150); });
  }

  global.IRAutocomplete = { attach: attach };
})(window);
