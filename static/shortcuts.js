(function () {
  'use strict';

  function relativeTime(isoString) {
    var diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
    if (diff < 60)           return 'just now';
    if (diff < 3600)         return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400)        return Math.floor(diff / 3600) + ' hr ago';
    if (diff < 86400 * 30)   return Math.floor(diff / 86400) + ' days ago';
    if (diff < 86400 * 365)  return Math.floor(diff / (86400 * 30)) + ' mo ago';
    return Math.floor(diff / (86400 * 365)) + ' yr ago';
  }

  function applyRelativeTimes() {
    document.querySelectorAll('time[datetime]').forEach(function (el) {
      var dt = el.getAttribute('datetime');
      if (dt) el.textContent = relativeTime(dt);
    });
  }

  document.addEventListener('DOMContentLoaded', applyRelativeTimes);
  document.addEventListener('htmx:afterSwap', applyRelativeTimes);

  function applyCategoryState() {
    document.querySelectorAll('details.sidebar-category[data-cat]').forEach(function (d) {
      var key = 'cat-open:' + d.getAttribute('data-cat');
      var stored = localStorage.getItem(key);
      if (stored === 'false') d.removeAttribute('open');
      else if (stored === 'true') d.setAttribute('open', '');
      if (d.dataset.persistBound) return;
      d.dataset.persistBound = '1';
      d.addEventListener('toggle', function () {
        localStorage.setItem(key, d.hasAttribute('open') ? 'true' : 'false');
      });
    });
  }
  document.addEventListener('DOMContentLoaded', applyCategoryState);
  document.addEventListener('htmx:afterSwap', applyCategoryState);

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

  var helpEl = null;
  function help() {
    if (!helpEl) helpEl = document.getElementById('shortcuts-help');
    return helpEl;
  }
  function helpOpen() {
    var h = help();
    return !!(h && !h.hasAttribute('hidden'));
  }
  function toggleHelp(force) {
    var h = help();
    if (!h) return;
    var open = typeof force === 'boolean' ? force : h.hasAttribute('hidden');
    if (open) h.removeAttribute('hidden'); else h.setAttribute('hidden', '');
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('[data-shortcuts-close]')) toggleHelp(false);
  });

  document.addEventListener('keydown', function (e) {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === 'Escape') {
      if (helpOpen()) { e.preventDefault(); toggleHelp(false); return; }
      if (focusedId) { e.preventDefault(); setFocus(null); return; }
      return;
    }

    if (helpOpen()) return;

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

      case 'g': {
        e.preventDefault();
        var all = cards();
        if (all.length) setFocus(all[0]);
        break;
      }

      case 'G': {
        e.preventDefault();
        var all2 = cards();
        if (all2.length) setFocus(all2[all2.length - 1]);
        break;
      }

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

      case 'x': {
        if (!el) return;
        e.preventDefault();
        var delBtn = el.querySelector('[hx-delete^="/articles/"]');
        if (delBtn) delBtn.click();
        break;
      }

      case 'r': {
        e.preventDefault();
        var refreshBtn = document.querySelector('[hx-post="/feeds/refresh-all"]');
        if (refreshBtn) refreshBtn.click();
        break;
      }

      case '?':
        e.preventDefault();
        toggleHelp();
        break;
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
