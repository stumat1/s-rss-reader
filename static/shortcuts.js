(function () {
  'use strict';

  var focusedId = null;

  function cards() {
    return Array.from(document.querySelectorAll('#articles .article-card'));
  }

  function setFocus(el) {
    document.querySelectorAll('.article-card--focused').forEach(function (c) {
      c.classList.remove('article-card--focused');
    });
    if (!el) { focusedId = null; return; }
    el.classList.add('article-card--focused');
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    focusedId = el.id || null;
  }

  function move(delta) {
    var all = cards();
    if (!all.length) return;
    var idx = focusedId ? all.findIndex(function (c) { return c.id === focusedId; }) : -1;
    setFocus(all[Math.max(0, Math.min(idx + delta, all.length - 1))]);
  }

  function current() {
    return focusedId ? document.getElementById(focusedId) : null;
  }

  // Clicking an article with the mouse sets it as the keyboard-nav anchor.
  document.addEventListener('click', function (e) {
    var card = e.target.closest && e.target.closest('.article-card');
    if (card) setFocus(card);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    var el = current();

    switch (e.key) {
      case 'j':
        e.preventDefault();
        move(1);
        break;

      case 'k':
        e.preventDefault();
        move(-1);
        break;

      case 'o': {
        if (!el) return;
        var a = el.querySelector('.article-title a');
        if (a) { e.preventDefault(); window.open(a.href, '_blank', 'noopener,noreferrer'); }
        break;
      }

      case 'f': {
        if (!el) return;
        e.preventDefault();
        var favBtn = el.querySelector('[hx-patch$="/favourite"]');
        if (favBtn) favBtn.click();
        break;
      }

      case 'u': {
        if (!el) return;
        e.preventDefault();
        var readBtn = el.querySelector('[hx-patch$="/read"]');
        if (readBtn) readBtn.click();
        break;
      }
    }
  });

  document.addEventListener('htmx:afterSwap', function (e) {
    // Feed navigation swaps the whole #articles container — reset focus.
    if (e.detail.target && e.detail.target.id === 'articles') {
      focusedId = null;
      return;
    }
    if (!focusedId) return;
    // fav/read toggle replaces the card via outerHTML — re-apply focus class.
    var el = document.getElementById(focusedId);
    if (el && el.classList.contains('article-card')) {
      el.classList.add('article-card--focused');
    } else {
      // Card was deleted or no longer present — clear focus.
      focusedId = null;
    }
  });
}());
