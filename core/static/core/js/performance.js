(function () {
  'use strict';
  var P = window.Panel, esc = P.esc;
  var base = '/api/v1/servers/' + document.getElementById('perf').dataset.server;
  var $ = function (s) { return document.querySelector(s); };
  var state = { recommended: {} };
  var LEVEL = { ok: ['success', 'bi-check-circle-fill'], info: ['info', 'bi-info-circle-fill'], warning: ['warning', 'bi-exclamation-triangle-fill'] };

  function fillDefaults(values) {
    document.querySelectorAll('#defaults-form [data-key]').forEach(function (el) {
      var v = values[el.dataset.key];
      if (el.type === 'checkbox') el.checked = v === 'true'; else el.value = v === undefined ? '' : v;
    });
  }

  async function check() {
    try {
      var d = await P.get(base + '/diagnostics/');
      state.recommended = d.recommended;
      $('#conn-alert').classList.add('d-none');
      $('#hints').innerHTML = d.hints.map(function (h) {
        var l = LEVEL[h.level] || LEVEL.info;
        return '<li class="list-group-item d-flex gap-2"><i class="bi ' + l[1] + ' text-' + l[0] + ' mt-1"></i><div>' + esc(h.message) + '</div></li>';
      }).join('');
      var a = d.aria2, e = d.effective, row = function (k, v, note) { return '<tr><th class="text-body-secondary fw-normal">' + k + '</th><td>' + esc(v) + (note ? ' <span class="badge text-bg-secondary">' + note + '</span>' : '') + '</td></tr>'; };
      $('#facts').innerHTML = row('Download limit', d.limits.download.human, d.limits.managed_by_panel ? 'managed by panel' : '') +
        row('Upload limit', d.limits.upload.human) +
        (d.limits.active_rule ? row('Active speed rule', d.limits.active_rule) : '') +
        row('Connections per server', e['max-connection-per-server'], d.defaults['max-connection-per-server'] ? 'panel default' : '') +
        row('Split', e.split, d.defaults.split ? 'panel default' : '') +
        row('Min split size', e['min-split-size'] , d.defaults['min-split-size'] ? 'panel default' : '') +
        row('File allocation', e['file-allocation'], d.defaults['file-allocation'] ? 'panel default' : '') +
        row('Downloads at once (aria2)', a['max-concurrent-downloads']) + row('Directory', a.dir);
      if (!state.formFilled) { fillDefaults(d.defaults); state.formFilled = true; }
    } catch (e) {
      $('#conn-alert span').textContent = e.message; $('#conn-alert').classList.remove('d-none');
    }
  }

  $('#recheck').addEventListener('click', check);
  $('#limits-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var body = {};
    Array.from(ev.target.elements).forEach(function (el) { if (el.name && el.value.trim() !== '') body[el.name] = el.value.trim(); });
    try { await P.post(base + '/global/', body); P.toast('Limits applied', 'success'); } catch (e) { P.toast(e.message, 'danger'); }
    check();
  });

  function collect() {
    var out = {};
    document.querySelectorAll('#defaults-form [data-key]').forEach(function (el) {
      if (el.type === 'checkbox') { if (el.checked) out[el.dataset.key] = 'true'; }
      else if (el.value.trim() !== '') out[el.dataset.key] = el.value.trim();
    });
    return out;
  }
  $('#defaults-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    try { var r = await P.post(base + '/defaults/', { defaults: collect() }); fillDefaults(r.defaults); P.toast('Defaults saved', 'success'); } catch (e) { P.toast(e.message, 'danger'); }
    check();
  });
  $('#use-recommended').addEventListener('click', function () { fillDefaults(state.recommended); P.toast('Recommended values filled in, press Save to apply', 'info'); });
  $('#clear-defaults').addEventListener('click', function () { fillDefaults({}); });
  check();
})();
