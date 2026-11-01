/* Inline form validation.
 *
 * Errors used to surface only after a round trip: typing two different
 * passwords meant a POST, a redirect and a flash message at the top of the
 * page to be told the second field didn't match the first. Feedback should
 * arrive where the problem is, while it can still be fixed.
 *
 * Progressive enhancement in both directions. `novalidate` is set here rather
 * than in the markup, so with JS off the browser's own validation still runs;
 * and the server re-checks everything regardless, because nothing a client
 * says about a password can be trusted.
 */
(function () {
  'use strict';

  var MESSAGES = {
    valueMissing: 'This field is required.',
    tooShort: function (el) {
      return 'Use at least ' + el.minLength + ' characters.';
    },
    typeMismatch: function (el) {
      return el.type === 'email'
        ? "That doesn't look like an email address."
        : 'That value isn’t in the right format.';
    }
  };

  function messageFor(el) {
    var v = el.validity;
    if (v.valid) return '';
    if (v.customError) return el.validationMessage;
    if (v.valueMissing) return MESSAGES.valueMissing;
    if (v.tooShort) return MESSAGES.tooShort(el);
    if (v.typeMismatch) return MESSAGES.typeMismatch(el);
    return 'Please check this field.';
  }

  // The <p> that carries the message, created next to the field's label.
  function slotFor(el) {
    var host = el.closest('label') || el;
    var slot = host.nextElementSibling;
    if (!slot || !slot.classList.contains('field-err')) {
      slot = document.createElement('p');
      slot.className = 'field-err';
      // Tie it to the input so a screen reader reads the reason, not just the
      // invalid state.
      slot.id = 'err-' + (el.name || Math.random().toString(36).slice(2));
      el.setAttribute('aria-describedby', slot.id);
      host.insertAdjacentElement('afterend', slot);
    }
    return slot;
  }

  function show(el) {
    var msg = messageFor(el);
    var slot = slotFor(el);
    slot.textContent = msg;
    slot.classList.toggle('on', !!msg);
    el.classList.toggle('invalid', !!msg);
    el.setAttribute('aria-invalid', msg ? 'true' : 'false');
    return !msg;
  }

  function clear(el) {
    var slot = slotFor(el);
    slot.classList.remove('on');
    el.classList.remove('invalid');
    el.setAttribute('aria-invalid', 'false');
  }

  document.querySelectorAll('form').forEach(function (form) {
    var fields = [].slice.call(
      form.querySelectorAll('input[required], input[minlength], input[type=email]'));
    if (!fields.length) return;

    // Confirm-password pairing: `password2` confirms `password`,
    // `new_password2` confirms `new_password`.
    var pairs = [];
    fields.forEach(function (el) {
      if (!/2$/.test(el.name || '')) return;
      var first = form.querySelector('[name="' + el.name.slice(0, -1) + '"]');
      if (first && first.type === 'password') pairs.push([first, el]);
    });

    function checkPair(first, second) {
      second.setCustomValidity(
        second.value && second.value !== first.value
          ? "The two passwords don’t match." : '');
    }

    pairs.forEach(function (p) {
      var first = p[0], second = p[1];
      [first, second].forEach(function (el) {
        el.addEventListener('input', function () {
          checkPair(first, second);
          // Only correct a message already on screen — don't start complaining
          // at someone mid-keystroke.
          if (second.classList.contains('invalid')) show(second);
        });
      });
    });

    fields.forEach(function (el) {
      // First feedback on blur: the field has been attempted.
      el.addEventListener('blur', function () {
        if (el.value || el.classList.contains('invalid')) show(el);
      });
      el.addEventListener('input', function () {
        if (el.classList.contains('invalid') && el.checkValidity()) clear(el);
      });
    });

    // Take over submit so the browser's bubble doesn't compete with the
    // inline messages. Set here, not in the markup, so no-JS keeps native
    // validation.
    form.noValidate = true;
    form.addEventListener('submit', function (e) {
      pairs.forEach(function (p) { checkPair(p[0], p[1]); });
      var bad = fields.filter(function (el) { return !show(el); });
      if (bad.length) {
        e.preventDefault();
        bad[0].focus();
      }
    });
  });
})();
