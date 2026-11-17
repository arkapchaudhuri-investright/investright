// The edition (landing page): the scroll-tracking index and the hover replays
// that draw each plate's data in. Vanilla, no libraries.
//
// Everything degrades: with JS off every section and value is simply there;
// under prefers-reduced-motion the replays never start (the CSS gates them
// too) and hover is the stylesheet's opacity cross-fade alone.
(function () {
  var ed = document.querySelector('.ed');
  if (!ed) return;
  var calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  var sections = [].slice.call(ed.querySelectorAll('.ed-sec'));
  var links = {};
  [].forEach.call(ed.querySelectorAll('[data-ed-link]'), function (a) {
    links[a.dataset.edLink] = a;
  });

  // --- Index: the section crossing the upper-middle of the viewport is the
  //     current one. A thin band (not a threshold) means exactly one wins.
  if ('IntersectionObserver' in window) {
    var current = null;
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        if (current) current.removeAttribute('aria-current');
        current = links[en.target.id] || null;
        if (current) current.setAttribute('aria-current', 'true');
      });
    }, { rootMargin: '-38% 0px -58% 0px' });
    sections.forEach(function (s) { spy.observe(s); });
  }

  // --- Replays. Entering a plate (pointer or keyboard focus) adds .replay,
  //     which the CSS turns into the data drawing itself in; numbers marked
  //     data-count count up alongside. The class comes off only when the
  //     longest animation finishes, so leaving early never snaps a half-drawn
  //     chart back to empty, and re-entering mid-replay doesn't restart it.
  function countUp(el) {
    var to = parseFloat(el.dataset.count);
    if (isNaN(to)) return;
    var dp = parseInt(el.dataset.dp || '0', 10);
    var w = 2 * Math.PI / 0.35, t0 = null;   // the same spring as --spring
    function frame(ts) {
      if (t0 === null) t0 = ts;
      var t = (ts - t0) / 1000;
      var x = t >= 0.5 ? 1 : 1 - (1 + w * t) * Math.exp(-w * t);
      el.textContent = (to * x).toLocaleString('en-US',
        { minimumFractionDigits: dp, maximumFractionDigits: dp, useGrouping: false });
      if (x < 1) requestAnimationFrame(frame);
      else el.textContent = el.dataset.final;
    }
    requestAnimationFrame(frame);
  }

  [].forEach.call(ed.querySelectorAll('.plate'), function (plate) {
    var counters = [].slice.call(plate.querySelectorAll('[data-count]'));
    counters.forEach(function (c) { c.dataset.final = c.textContent; });
    var busy = false;
    function play() {
      if (busy || calm.matches) return;
      busy = true;
      plate.classList.add('replay');
      counters.forEach(countUp);
      // The longest replay (sweep + stagger) is under 1.2s; a timer is the
      // dependable end signal, since animationend bubbles from every child.
      setTimeout(function () { plate.classList.remove('replay'); busy = false; }, 1200);
    }
    plate.addEventListener('pointerenter', function (e) {
      if (e.pointerType === 'mouse' || e.pointerType === 'pen') play();
    });
    plate.addEventListener('focusin', play);
  });

  // --- News tabs. Every rail is already in the DOM, so this only toggles
  //     which one shows — with JS off the first topic is visible.
  var tabs = [].slice.call(ed.querySelectorAll('.nw-tab'));
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.setAttribute('aria-selected', String(t === tab)); });
      [].forEach.call(ed.querySelectorAll('[data-nw-panel]'), function (p) {
        p.toggleAttribute('hidden', p.dataset.nwPanel !== tab.dataset.nw);
      });
    });
  });

  // "Search a company" scrolls back to the hero and puts the cursor in it.
  [].forEach.call(ed.querySelectorAll('[data-ed-search]'), function (a) {
    a.addEventListener('click', function (e) {
      var input = document.getElementById('symbol');
      if (!input) return;
      e.preventDefault();
      input.scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth', block: 'center' });
      input.focus({ preventScroll: true });
    });
  });
})();
