// Loaded in <head>, before first paint, so there is no flash of the wrong theme.
(function () {
  'use strict';
  var KEY = 'panel-theme';
  var root = document.documentElement;
  var dark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function mode() { return stored() || root.getAttribute('data-default-theme') || 'auto'; }
  function resolved(m) { return m === 'auto' ? (dark && dark.matches ? 'dark' : 'light') : m; }
  function apply() { root.setAttribute('data-bs-theme', resolved(mode())); }

  apply();
  if (dark && dark.addEventListener) dark.addEventListener('change', function () { apply(); document.dispatchEvent(new Event('themechange')); });

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var icons = { auto: 'bi-circle-half', light: 'bi-sun-fill', dark: 'bi-moon-stars-fill' };
    function paint() { btn.innerHTML = '<i class="bi ' + icons[mode()] + '"></i>'; btn.title = 'Theme: ' + mode(); }
    paint();
    btn.addEventListener('click', function () {
      var order = ['auto', 'light', 'dark'];
      var next = order[(order.indexOf(mode()) + 1) % order.length];
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
      apply(); paint();
      document.dispatchEvent(new Event('themechange'));
    });
  });
})();
