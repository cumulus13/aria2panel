// Small shared helpers: CSRF-aware fetch, formatting, escaping, toasts.
window.Panel = (function () {
  'use strict';

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  async function request(url, opts) {
    opts = opts || {};
    var headers = { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf() };
    var body;
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
    else if (opts.form) { body = opts.form; }
    var res = await fetch(url, { method: opts.method || 'GET', headers: headers, body: body, credentials: 'same-origin' });
    var data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (res.status === 401) { window.location.href = document.body.dataset.loginUrl + '?next=' + encodeURIComponent(location.pathname); throw new Error('Signed out'); }
    if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
    return data;
  }

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  function fmtBytes(n) {
    n = Number(n) || 0;
    var u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2)) + ' ' + u[i];
  }
  function fmtSpeed(n) { return fmtBytes(n) + '/s'; }
  function fmtEta(s) {
    if (s == null) return '–';
    s = Math.round(s);
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'm';
    if (m) return m + 'm ' + x + 's';
    return x + 's';
  }
  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function toast(message, type) {
    var box = document.getElementById('toasts');
    if (!box || !window.bootstrap) { return; }
    type = type || 'primary';
    var el = document.createElement('div');
    el.className = 'toast align-items-center text-bg-' + type + ' border-0';
    el.setAttribute('role', 'alert');
    el.innerHTML = '<div class="d-flex"><div class="toast-body">' + esc(message) + '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    box.appendChild(el);
    var t = new bootstrap.Toast(el, { delay: type === 'danger' ? 7000 : 3500 });
    el.addEventListener('hidden.bs.toast', function () { el.remove(); });
    t.show();
  }

  function modal(id) { return bootstrap.Modal.getOrCreateInstance(document.getElementById(id)); }

  return {
    get: function (u) { return request(u); },
    post: function (u, j) { return request(u, { method: 'POST', json: j || {} }); },
    postForm: function (u, f) { return request(u, { method: 'POST', form: f }); },
    esc: esc, fmtBytes: fmtBytes, fmtSpeed: fmtSpeed, fmtEta: fmtEta, fmtDate: fmtDate, toast: toast, modal: modal
  };
})();
