(function () {
  'use strict';
  var P = window.Panel, esc = P.esc;
  var root = document.getElementById('sched');
  var base = '/api/v1/servers/' + root.dataset.server;
  var $ = function (s) { return document.querySelector(s); };
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var state = { data: null, formLoaded: false, ruleId: null, whenId: null, editing: false };

  function dayLabel(days) {
    if (days.length === 7) return 'Every day';
    if (days.join() === '0,1,2,3,4') return 'Weekdays';
    if (days.join() === '5,6') return 'Weekend';
    return days.map(function (d) { return DAYS[d]; }).join(', ');
  }
  function limit(v) { return v === '0' ? '<span class="text-body-secondary">unlimited</span>' : esc(v); }
  var KIND = { uri: 'bi-file-earmark-arrow-down-fill', torrent: 'bi-magnet-fill', metalink: 'bi-diagram-3-fill' };
  var HSTAT = { dispatched: ['success', 'bi-check-circle-fill', 'started'], failed: ['danger', 'bi-x-circle-fill', 'failed'], cancelled: ['secondary', 'bi-slash-circle', 'cancelled'] };

  function render(d) {
    state.data = d;
    var can = d.can_edit;
    $('#s-now').textContent = d.now;
    if (!state.formLoaded || !state.editing) {
      $('#cfg-enabled').checked = d.config.enabled;
      $('#cfg-max').value = d.config.max_downloads;
      $('#cfg-enforce').checked = d.config.enforce_in_aria2;
      $('#cfg-dl').value = d.config.default_download_limit;
      $('#cfg-ul').value = d.config.default_upload_limit;
      state.formLoaded = true;
    }
    if (d.running === null) { $('#s-running').textContent = 'aria2 unreachable'; $('#s-bar').style.width = '0%'; }
    else {
      $('#s-running').textContent = d.config.enabled ? d.running + ' / ' + d.config.max_downloads : d.running + ' running (no limit)';
      $('#s-bar').style.width = d.config.enabled ? Math.min(100, d.running * 100 / d.config.max_downloads) + '%' : '0%';
    }
    $('#conn-alert').classList.toggle('d-none', !d.aria2_error);
    if (d.aria2_error) $('#conn-alert span').textContent = d.aria2_error;

    $('#q-count').textContent = d.queue.length;
    $('#q-empty').classList.toggle('d-none', d.queue.length > 0);
    $('#q-clear').classList.toggle('d-none', !can || !d.queue.length);
    var last = d.queue.length - 1;
    $('#q-rows').innerHTML = d.queue.map(function (q, i) {
      var b = function (op, icon, title, dis, cls) { return '<button class="btn btn-sm ' + (cls || 'btn-outline-secondary') + '" data-op="' + op + '" data-id="' + q.id + '" title="' + title + '"' + (dis ? ' disabled' : '') + '><i class="bi ' + icon + '"></i></button>'; };
      return '<tr><td class="text-body-secondary">' + (i + 1) + '</td><td><div class="d-flex align-items-center gap-2"><span class="dl-icon"><i class="bi ' + (KIND[q.kind] || KIND.uri) + '"></i></span><div><div class="fw-semibold text-break">' + esc(q.name) + '</div>' + (q.dir ? '<div class="small text-body-secondary">' + esc(q.dir) + '</div>' : '') + '</div></div></td>' +
        '<td>' + (q.not_before ? '<span class="badge text-bg-info">' + esc(P.fmtDate(q.not_before)) + '</span>' : '<span class="text-body-secondary small">next free slot</span>') + '</td>' +
        '<td class="text-end"><div class="btn-group">' + b('top', 'bi-chevron-bar-up', 'Move to top', i === 0) + b('up', 'bi-chevron-up', 'Move up', i === 0) + b('down', 'bi-chevron-down', 'Move down', i === last) + b('bottom', 'bi-chevron-bar-down', 'Move to bottom', i === last) + '</div> ' +
        '<div class="btn-group">' + b('schedule', 'bi-clock', 'Set start time') + b('start', 'bi-lightning-charge-fill', 'Start now', false, 'btn-outline-primary') + b('remove', 'bi-x-lg', 'Remove', false, 'btn-outline-danger') + '</div></td></tr>';
    }).join('');

    $('#r-empty').classList.toggle('d-none', d.rules.length > 0);
    $('#r-add').classList.toggle('d-none', !can);
    $('#r-rows').innerHTML = d.rules.map(function (r) {
      return '<tr class="' + (r.enabled ? '' : 'text-body-secondary') + '"><td class="fw-semibold">' + esc(r.name) + (r.enabled ? '' : ' <span class="badge text-bg-secondary">off</span>') + '</td><td>' + esc(dayLabel(r.days)) + '</td><td class="num">' + esc(r.start_time) + ' – ' + esc(r.end_time) + '</td><td>' + limit(r.download_limit) + '</td><td>' + limit(r.upload_limit) + '</td>' +
        '<td class="text-end">' + (can ? '<button class="btn btn-sm btn-outline-secondary" data-rule="' + r.id + '"><i class="bi bi-pencil"></i></button>' : '') + '</td></tr>';
    }).join('');

    $('#h-rows').innerHTML = d.recent.length ? d.recent.map(function (i) {
      var s = HSTAT[i.status] || ['secondary', 'bi-circle', i.status];
      return '<li class="list-group-item d-flex justify-content-between align-items-center gap-2"><div class="text-break"><i class="bi ' + s[1] + ' text-' + s[0] + '"></i> ' + esc(i.name) + (i.error ? '<div class="small text-danger">' + esc(i.error) + '</div>' : '') + '</div><span class="small text-body-secondary text-nowrap">' + s[2] + ' ' + esc(P.fmtDate(i.dispatched_at || i.created_at)) + '</span></li>';
    }).join('') : '<li class="list-group-item text-body-secondary small">Nothing yet</li>';
  }

  async function refresh() {
    try { render(await P.get(base + '/scheduler/')); }
    catch (e) { $('#conn-alert span').textContent = e.message; $('#conn-alert').classList.remove('d-none'); }
  }

  ['#cfg-enabled', '#cfg-enforce', '#cfg-max', '#cfg-dl', '#cfg-ul'].forEach(function (s) { $(s).addEventListener('input', function () { state.editing = true; }); });
  $('#cfg-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    try {
      render(await P.post(base + '/scheduler/', {
        enabled: $('#cfg-enabled').checked, enforce_in_aria2: $('#cfg-enforce').checked, max_downloads: parseInt($('#cfg-max').value, 10),
        default_download_limit: $('#cfg-dl').value.trim() || '0', default_upload_limit: $('#cfg-ul').value.trim() || '0'
      }));
      state.editing = false; state.formLoaded = false; render(state.data);
      P.toast('Scheduler saved', 'success');
    } catch (e) { P.toast(e.message, 'danger'); }
  });

  $('#q-rows').addEventListener('click', async function (ev) {
    var b = ev.target.closest('button[data-op]');
    if (!b) return;
    var op = b.dataset.op, id = parseInt(b.dataset.id, 10);
    if (op === 'schedule') {
      state.whenId = id;
      var item = state.data.queue.filter(function (q) { return q.id === id; })[0];
      $('#when-input').value = item && item.not_before ? toLocalInput(item.not_before) : '';
      return P.modal('whenModal').show();
    }
    try { await P.post(base + '/queue/', { op: op, id: id }); if (op === 'start') P.toast('Started', 'success'); }
    catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  });
  function toLocalInput(iso) { var d = new Date(iso), p = function (n) { return String(n).padStart(2, '0'); }; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()); }
  $('#when-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var v = $('#when-input').value;
    try { await P.post(base + '/queue/', { op: 'schedule', id: state.whenId, not_before: v ? new Date(v).toISOString() : null }); P.modal('whenModal').hide(); }
    catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  });
  $('#q-clear').addEventListener('click', async function () {
    if (!confirm('Remove every queued download?')) return;
    try { await P.post(base + '/queue/', { op: 'clear' }); } catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  });

  // speed rules
  function openRule(r) {
    state.ruleId = r ? r.id : null;
    $('#rule-title').textContent = r ? 'Edit rule' : 'New speed rule';
    $('#rule-name').value = r ? r.name : '';
    document.querySelectorAll('.rule-day').forEach(function (c) { c.checked = r ? r.days.indexOf(parseInt(c.value, 10)) !== -1 : true; });
    $('#rule-start').value = r ? r.start_time : '09:00';
    $('#rule-end').value = r ? r.end_time : '17:00';
    $('#rule-dl').value = r ? r.download_limit : '0';
    $('#rule-ul').value = r ? r.upload_limit : '0';
    $('#rule-enabled').checked = r ? r.enabled : true;
    $('#rule-delete').classList.toggle('d-none', !r);
    P.modal('ruleModal').show();
  }
  $('#r-add').addEventListener('click', function () { openRule(null); });
  $('#r-rows').addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-rule]');
    if (b) openRule(state.data.rules.filter(function (r) { return r.id === parseInt(b.dataset.rule, 10); })[0]);
  });
  $('#rule-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    try {
      await P.post(base + '/rules/', {
        op: 'save', id: state.ruleId, name: $('#rule-name').value,
        days: Array.from(document.querySelectorAll('.rule-day:checked')).map(function (c) { return parseInt(c.value, 10); }),
        start_time: $('#rule-start').value, end_time: $('#rule-end').value,
        download_limit: $('#rule-dl').value.trim() || '0', upload_limit: $('#rule-ul').value.trim() || '0', enabled: $('#rule-enabled').checked
      });
      P.modal('ruleModal').hide(); P.toast('Rule saved', 'success');
    } catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  });
  $('#rule-delete').addEventListener('click', async function () {
    if (!confirm('Delete this rule?')) return;
    try { await P.post(base + '/rules/', { op: 'delete', id: state.ruleId }); P.modal('ruleModal').hide(); }
    catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  });

  refresh();
  setInterval(function () { if (!document.hidden) refresh(); }, 2000);
})();
