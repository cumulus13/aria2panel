(function () {
  'use strict';
  var P = window.Panel, esc = P.esc;
  var root = document.getElementById('dashboard');
  var serverId = root.dataset.server;
  var poll = parseInt(root.dataset.poll, 10) || 1500;
  var isStaff = root.dataset.staff === '1';
  var base = '/api/servers/' + serverId;
  var $ = function (s) { return document.querySelector(s); };

  var RANK = { active: 0, waiting: 1, queued: 2, paused: 3, error: 4, complete: 5, removed: 6 };
  var STATUS = {
    active: ['primary', 'bi-arrow-down-circle-fill', 'Downloading'],
    seeding: ['success', 'bi-arrow-up-circle-fill', 'Seeding'],
    waiting: ['secondary', 'bi-hourglass-split', 'Waiting'],
    queued: ['info', 'bi-list-ol', 'Queued'],
    paused: ['warning', 'bi-pause-circle-fill', 'Paused'],
    complete: ['success', 'bi-check-circle-fill', 'Completed'],
    error: ['danger', 'bi-exclamation-triangle-fill', 'Error'],
    removed: ['dark', 'bi-trash', 'Removed']
  };
  var BAR = { active: 'bg-primary progress-bar-striped progress-bar-animated', seeding: 'bg-success', waiting: 'bg-secondary', queued: 'bg-info', paused: 'bg-warning', complete: 'bg-success', error: 'bg-danger', removed: 'bg-dark' };

  var state = { filter: 'all', search: '', items: [], selected: new Set(), cache: {}, order: '', down: [], up: [], timer: null, failures: 0 };
  var rows = $('#rows');

  function rank(d) { return RANK[d.status] !== undefined ? RANK[d.status] : 9; }
  function key(d) { return d.status === 'active' && d.seeding ? 'seeding' : d.status; }
  function matches(d) {
    var f = state.filter;
    if (f !== 'all' && d.status !== f) return false;
    if (state.search && d.name.toLowerCase().indexOf(state.search) === -1) return false;
    return true;
  }

  // ---------------------------------------------------------------- rows
  function actions(d) {
    var g = esc(d.gid), b = function (act, icon, title, cls) {
      return '<button class="btn btn-sm ' + (cls || 'btn-outline-secondary') + '" data-act="' + act + '" data-gid="' + g + '" title="' + title + '"><i class="bi ' + icon + '"></i></button>';
    };
    var out = [];
    if (d.status === 'active' || d.status === 'waiting') out.push(b('pause', 'bi-pause-fill', 'Pause'));
    if (d.status === 'paused') out.push(b('unpause', 'bi-play-fill', 'Resume'));
    if (d.status === 'queued') out.push(b('start', 'bi-lightning-charge-fill', 'Start now'));
    if (d.status === 'error') out.push(b('retry', 'bi-arrow-repeat', 'Retry'));
    if (!d.queued) out.push(b('details', 'bi-info-circle', 'Details'));
    out.push(b('remove', d.status === 'complete' || d.status === 'error' || d.status === 'removed' ? 'bi-trash' : 'bi-x-lg', d.queued ? 'Cancel' : 'Remove', 'btn-outline-danger'));
    return '<div class="btn-group row-actions">' + out.join('') + '</div>';
  }

  function inner(d) {
    var k = key(d), st = STATUS[k] || ['secondary', 'bi-question-circle', k];
    var icon = d.torrent ? 'bi-magnet-fill' : (d.kind === 'metalink' ? 'bi-diagram-3-fill' : 'bi-file-earmark-arrow-down-fill');
    var sub;
    if (d.status === 'error') sub = '<div class="sub err" title="' + esc(d.errorMessage) + '">' + esc(d.errorMessage || ('error ' + d.errorCode)) + '</div>';
    else if (d.status === 'queued') sub = '<div class="sub">' + (d.notBefore ? 'starts ' + esc(P.fmtDate(d.notBefore)) : 'waiting for a free slot') + ' · #' + d.position + (d.dir ? ' · ' + esc(d.dir) : '') + '</div>';
    else sub = '<div class="sub">' + esc(d.dir) + (d.connections ? ' · ' + d.connections + ' conn' : '') + (d.torrent && d.seeders ? ' · ' + d.seeders + ' seeds' : '') + '</div>';
    var size = d.total ? P.fmtBytes(d.completed) + ' / ' + P.fmtBytes(d.total) : (d.status === 'queued' ? '–' : P.fmtBytes(d.completed));
    var pct = d.queued ? 0 : d.progress;
    var speed = (d.downloadSpeed ? '<div><i class="bi bi-arrow-down text-primary"></i> ' + P.fmtSpeed(d.downloadSpeed) + '</div>' : '') + (d.uploadSpeed ? '<div><i class="bi bi-arrow-up text-success"></i> ' + P.fmtSpeed(d.uploadSpeed) + '</div>' : '') || '–';
    return '<td><input class="form-check-input row-check" type="checkbox" data-gid="' + esc(d.gid) + '"' + (state.selected.has(d.gid) ? ' checked' : '') + '></td>' +
      '<td class="dl-name"><div class="d-flex align-items-center gap-2"><span class="dl-icon"><i class="bi ' + icon + '"></i></span><div class="min-w-0"><div class="name" title="' + esc(d.name) + '">' + esc(d.name) + '</div>' + sub + '</div></div></td>' +
      '<td class="text-end num d-none d-md-table-cell">' + size + '</td>' +
      '<td><div class="d-flex align-items-center gap-2"><div class="progress flex-grow-1" style="height:8px"><div class="progress-bar ' + BAR[k] + '" style="width:' + (k === 'complete' ? 100 : pct) + '%"></div></div><span class="pct">' + (k === 'complete' ? '100' : pct.toFixed(1)) + '%</span></div></td>' +
      '<td class="text-end num d-none d-lg-table-cell">' + speed + '</td>' +
      '<td class="text-end num d-none d-lg-table-cell">' + (d.status === 'active' ? P.fmtEta(d.eta) : '–') + '</td>' +
      '<td><span class="badge status-badge text-bg-' + st[0] + '"><i class="bi ' + st[1] + '"></i> ' + st[2] + '</span></td>' +
      '<td class="text-end">' + actions(d) + '</td>';
  }

  function renderRows(visible) {
    var order = visible.map(function (d) { return d.gid; }).join(',');
    if (order !== state.order) {
      rows.innerHTML = visible.map(function (d) {
        return '<tr data-gid="' + esc(d.gid) + '"' + (state.selected.has(d.gid) ? ' class="selected"' : '') + '>' + inner(d) + '</tr>';
      }).join('');
      state.order = order;
      state.cache = {};
      visible.forEach(function (d) { state.cache[d.gid] = JSON.stringify(d); });
      return;
    }
    var trs = rows.children;
    visible.forEach(function (d, i) {
      var sig = JSON.stringify(d);
      if (state.cache[d.gid] !== sig) { state.cache[d.gid] = sig; trs[i].innerHTML = inner(d); }
    });
  }

  // ---------------------------------------------------------------- sparkline
  function drawSpark() {
    var c = $('#spark');
    if (!c || !c.getContext) return;
    var dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    if (!w) return;
    c.width = w * dpr; c.height = h * dpr;
    var g = c.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    var cs = getComputedStyle(document.documentElement);
    var max = Math.max(1024, Math.max.apply(null, state.down.concat(state.up)));
    function line(data, color, fill) {
      if (data.length < 2) return;
      var step = w / (60 - 1), x0 = w - (data.length - 1) * step;
      g.beginPath();
      data.forEach(function (v, i) { var x = x0 + i * step, y = h - 3 - (v / max) * (h - 8); i ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.strokeStyle = color; g.lineWidth = 2; g.lineJoin = 'round'; g.stroke();
      if (fill) {
        g.lineTo(w, h); g.lineTo(x0, h); g.closePath();
        var gr = g.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, color + '55'); gr.addColorStop(1, color + '00');
        g.fillStyle = gr; g.fill();
      }
    }
    var primary = (cs.getPropertyValue('--bs-primary') || '#0d6efd').trim(), success = (cs.getPropertyValue('--bs-success') || '#198754').trim();
    line(state.up, success, false);
    line(state.down, primary, true);
  }

  // ---------------------------------------------------------------- render
  function render(data) {
    var g = data.global, s = data.scheduler;
    $('#st-down').textContent = P.fmtSpeed(g.downloadSpeed);
    $('#st-up').textContent = P.fmtSpeed(g.uploadSpeed);
    $('#st-active').textContent = g.numActive;
    $('#st-waiting').textContent = g.numWaiting + ' waiting';
    $('#st-queued').textContent = s.queued;
    $('#st-stopped').textContent = g.numStopped + ' finished';
    $('#st-slots').textContent = s.enabled ? s.running + ' / ' + s.max : 'off';
    $('#st-slots-bar').style.width = s.enabled ? Math.min(100, s.running * 100 / s.max) + '%' : '0%';
    $('#st-sched-state').textContent = s.enabled ? (s.queued ? s.queued + ' waiting for a slot' : 'slots available') : 'no limit, everything starts at once';
    state.down.push(g.downloadSpeed); state.up.push(g.uploadSpeed);
    if (state.down.length > 60) { state.down.shift(); state.up.shift(); }
    drawSpark();

    state.items = data.downloads.slice().sort(function (a, b) { return rank(a) - rank(b); });
    renderList();
  }

  function renderList() {
    var counts = { all: state.items.length, active: 0, waiting: 0, queued: 0, paused: 0, complete: 0, error: 0 };
    var present = {};
    state.items.forEach(function (d) { present[d.gid] = 1; if (counts[d.status] !== undefined) counts[d.status]++; });
    Object.keys(counts).forEach(function (k) { var el = document.querySelector('[data-count="' + k + '"]'); if (el) el.textContent = counts[k]; });
    state.selected.forEach(function (gid) { if (!present[gid]) state.selected.delete(gid); });

    var visible = state.items.filter(matches);
    renderRows(visible);
    $('#empty').classList.toggle('d-none', visible.length > 0);
    updateBulk();
  }

  function updateBulk() {
    $('#bulk').classList.toggle('d-none', state.selected.size === 0);
    $('#bulk-count').textContent = state.selected.size;
    var visible = state.items.filter(matches);
    var all = $('#check-all');
    all.checked = visible.length > 0 && visible.every(function (d) { return state.selected.has(d.gid); });
  }

  // ---------------------------------------------------------------- polling
  async function refresh() {
    clearTimeout(state.timer);
    var wait = poll;
    try {
      render(await P.get(base + '/snapshot/'));
      state.failures = 0;
      $('#conn-alert').classList.add('d-none');
    } catch (e) {
      state.failures++;
      $('#conn-alert span').textContent = e.message;
      $('#conn-alert').classList.remove('d-none');
      wait = Math.min(15000, poll * (1 + state.failures));
    }
    state.timer = setTimeout(refresh, document.hidden ? Math.max(wait, 5000) : wait);
  }

  // ---------------------------------------------------------------- actions
  async function act(action, gids, quiet) {
    try {
      var r = await P.post(base + '/action/', gids ? { action: action, gids: gids } : { action: action });
      if (r.errors && r.errors.length) P.toast(r.errors.join('\n'), 'danger');
      else if (!quiet) P.toast('Done', 'success');
    } catch (e) { P.toast(e.message, 'danger'); }
    refresh();
  }

  function find(gid) { return state.items.filter(function (d) { return d.gid === gid; })[0]; }

  rows.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-act]');
    if (btn) {
      var gid = btn.dataset.gid, d = find(gid), a = btn.dataset.act;
      if (a === 'details') return showDetails(gid);
      if (a === 'remove' && d && ['active', 'waiting', 'paused'].indexOf(d.status) !== -1 && !confirm('Remove "' + d.name + '"? Downloaded data stays on disk.')) return;
      return act(a, [gid], true);
    }
    var chk = ev.target.closest('.row-check');
    if (chk) {
      chk.checked ? state.selected.add(chk.dataset.gid) : state.selected.delete(chk.dataset.gid);
      chk.closest('tr').classList.toggle('selected', chk.checked);
      updateBulk();
    }
  });
  $('#check-all').addEventListener('change', function (ev) {
    state.items.filter(matches).forEach(function (d) { ev.target.checked ? state.selected.add(d.gid) : state.selected.delete(d.gid); });
    state.order = '';
    renderList();
  });

  document.querySelectorAll('[data-bulk]').forEach(function (b) {
    b.addEventListener('click', function () {
      var gids = Array.from(state.selected);
      if (!gids.length) return;
      if (b.dataset.bulk === 'remove' && !confirm('Remove ' + gids.length + ' download(s)?')) return;
      act(b.dataset.bulk, gids);
      state.selected.clear();
    });
  });
  $('#btn-pause-all').addEventListener('click', function () { act('pauseAll'); });
  $('#btn-resume-all').addEventListener('click', function () { act('unpauseAll'); });
  $('#btn-purge').addEventListener('click', function () { if (confirm('Clear all finished downloads from the list?')) act('purge'); });

  $('#filters').addEventListener('click', function (ev) {
    var a = ev.target.closest('[data-filter]');
    if (!a) return;
    ev.preventDefault();
    document.querySelectorAll('#filters .nav-link').forEach(function (n) { n.classList.remove('active'); });
    a.classList.add('active');
    state.filter = a.dataset.filter; state.order = '';
    renderList();
  });
  var searchTimer;
  $('#search').addEventListener('input', function (ev) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.search = ev.target.value.trim().toLowerCase(); state.order = '';
      renderList();
    }, 120);
  });

  // ---------------------------------------------------------------- add
  var addForm = $('#add-form');
  function openAdd(text, files) {
    if (text) $('#add-uris').value = text;
    if (files && files.length) $('#add-files').files = files;
    P.modal('addModal').show();
    setTimeout(function () { $('#add-uris').focus(); }, 300);
  }
  $('#btn-add').addEventListener('click', function () { openAdd(); });

  addForm.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var fd = new FormData();
    fd.append('uris', $('#add-uris').value);
    Array.from($('#add-files').files).forEach(function (f) { fd.append('files', f); });
    var opts = {};
    addForm.querySelectorAll('[data-opt]').forEach(function (i) { if (i.value.trim()) opts[i.dataset.opt] = i.value.trim(); });
    if ($('#add-paused').checked) opts.pause = 'true';
    if ($('#add-verify').checked) opts['check-integrity'] = 'true';
    fd.append('options', JSON.stringify(opts));
    if ($('#add-when').value) fd.append('not_before', new Date($('#add-when').value).toISOString());
    var btn = $('#add-submit');
    btn.disabled = true;
    try {
      var r = await P.postForm(base + '/add/', fd);
      var parts = [];
      if (r.dispatched) parts.push(r.dispatched + ' started');
      if (r.queued) parts.push(r.queued + ' queued');
      if (r.failed) parts.push(r.failed + ' failed');
      P.toast(parts.join(' · ') || 'Nothing added', r.failed ? 'warning' : 'success');
      if (r.errors && r.errors.length) P.toast(r.errors.join('\n'), 'danger');
      addForm.reset();
      P.modal('addModal').hide();
      refresh();
    } catch (e) { P.toast(e.message, 'danger'); }
    btn.disabled = false;
  });

  // drag & drop torrent files, paste links
  var overlay = document.createElement('div');
  overlay.className = 'drop-overlay';
  overlay.innerHTML = '<div><i class="bi bi-cloud-arrow-up-fill"></i> Drop torrent / metalink files</div>';
  document.body.appendChild(overlay);
  var dragDepth = 0;
  window.addEventListener('dragenter', function (e) { if (e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') !== -1) { dragDepth++; overlay.classList.add('show'); } });
  window.addEventListener('dragleave', function () { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) overlay.classList.remove('show'); });
  window.addEventListener('dragover', function (e) { e.preventDefault(); });
  window.addEventListener('drop', function (e) {
    e.preventDefault(); dragDepth = 0; overlay.classList.remove('show');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) openAdd('', e.dataTransfer.files);
  });
  window.addEventListener('paste', function (e) {
    if (/^(input|textarea|select)$/i.test((e.target.tagName || '')) || document.querySelector('.modal.show')) return;
    var text = (e.clipboardData && e.clipboardData.getData('text')) || '';
    if (/^\s*(https?:\/\/|ftp:\/\/|magnet:)/i.test(text)) { e.preventDefault(); openAdd(text.trim()); }
  });

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || /^(input|textarea|select)$/i.test(e.target.tagName) || document.querySelector('.modal.show')) return;
    if (e.key === '/') { e.preventDefault(); $('#search').focus(); }
    if (e.key === 'a') { e.preventDefault(); openAdd(); }
  });

  // ---------------------------------------------------------------- details
  async function showDetails(gid) {
    $('#detail-title').textContent = 'Loading…';
    $('#detail-body').innerHTML = '<div class="text-center py-5"><div class="spinner-border"></div></div>';
    P.modal('detailModal').show();
    try {
      var d = await P.get(base + '/download/' + encodeURIComponent(gid) + '/');
      var s = d.summary, k = key(s);
      $('#detail-title').textContent = s.name;
      var kv = function (rows) { return '<table class="table table-sm mb-0"><tbody>' + rows.map(function (r) { return '<tr><th class="text-body-secondary fw-normal" style="width:11rem">' + r[0] + '</th><td class="text-break">' + r[1] + '</td></tr>'; }).join('') + '</tbody></table>'; };
      var overview = kv([
        ['Status', '<span class="badge text-bg-' + (STATUS[k] || ['secondary'])[0] + '">' + ((STATUS[k] || [0, 0, k])[2]) + '</span>' + (s.errorMessage ? ' <span class="text-danger">' + esc(s.errorMessage) + '</span>' : '')],
        ['Progress', s.progress.toFixed(2) + '% (' + P.fmtBytes(s.completed) + ' of ' + P.fmtBytes(s.total) + ')'],
        ['Speed', '↓ ' + P.fmtSpeed(s.downloadSpeed) + ' · ↑ ' + P.fmtSpeed(s.uploadSpeed)],
        ['ETA', P.fmtEta(s.eta)], ['Connections', s.connections + (s.torrent ? ' · ' + s.seeders + ' seeders' : '')],
        ['Directory', '<code>' + esc(d.dir || s.dir) + '</code>'], ['GID', '<code>' + esc(s.gid) + '</code>']
      ].concat(Object.keys(d.info).map(function (i) { return [esc(i), esc(d.info[i])]; })));
      var files = '<table class="table table-sm align-middle mb-0"><thead><tr><th>#</th><th>Path</th><th class="text-end">Size</th><th style="width:140px">Done</th></tr></thead><tbody>' +
        d.files.map(function (f) { return '<tr class="' + (f.selected ? '' : 'text-body-secondary') + '"><td>' + esc(f.index) + '</td><td class="text-break">' + esc(f.path || '(pending)') + '</td><td class="text-end num">' + P.fmtBytes(f.length) + '</td><td><div class="progress" style="height:6px"><div class="progress-bar" style="width:' + f.progress + '%"></div></div></td></tr>'; }).join('') + '</tbody></table>';
      var opts = kv(Object.keys(d.options).map(function (o) { return ['<code>' + esc(o) + '</code>', esc(d.options[o])]; }));
      $('#detail-body').innerHTML = '<ul class="nav nav-tabs mb-3"><li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#dt-o">Overview</button></li><li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#dt-f">Files <span class="badge text-bg-secondary">' + d.files.length + '</span></button></li><li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#dt-p">Options</button></li></ul>' +
        '<div class="tab-content"><div class="tab-pane fade show active" id="dt-o">' + overview + '</div><div class="tab-pane fade" id="dt-f">' + files + '</div><div class="tab-pane fade" id="dt-p">' + opts + '</div></div>';
    } catch (e) {
      $('#detail-title').textContent = 'Details';
      $('#detail-body').innerHTML = '<div class="alert alert-danger mb-0">' + esc(e.message) + '</div>';
    }
  }

  // ---------------------------------------------------------------- global limits (staff)
  var limitsBtn = $('#btn-limits');
  if (limitsBtn && isStaff) {
    var lf = $('#limits-form');
    limitsBtn.addEventListener('click', async function () {
      try {
        var o = await P.get(base + '/global/');
        Object.keys(o).forEach(function (k) { if (lf.elements[k]) lf.elements[k].value = o[k]; });
        P.modal('limitsModal').show();
      } catch (e) { P.toast(e.message, 'danger'); }
    });
    lf.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var body = {};
      Array.from(lf.elements).forEach(function (el) { if (el.name && el.value !== '') body[el.name] = el.value; });
      try { await P.post(base + '/global/', body); P.toast('Limits applied', 'success'); P.modal('limitsModal').hide(); }
      catch (e) { P.toast(e.message, 'danger'); }
    });
  }

  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  document.addEventListener('themechange', drawSpark);
  window.addEventListener('resize', drawSpark);
  refresh();
})();
