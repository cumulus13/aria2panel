// Time based speed chart (canvas). Points are [epochMs, downloadBytesPerSec, uploadBytesPerSec].
(function () {
  'use strict';
  var P = window.Panel;

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  // power of two ceiling so the axis labels land on KiB/MiB boundaries
  function niceMax(v) { return Math.pow(2, Math.ceil(Math.log2(Math.max(v, 16 * 1024)))); }
  function clock(t) { var d = new Date(t), p = function (n) { return String(n).padStart(2, '0'); }; return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()); }
  function span(ms) { var s = Math.round(ms / 1000); return s >= 120 ? Math.round(s / 60) + ' min' : s + ' s'; }

  /* o: windowMs, end, axis, gapMs, hover (px), empty (text), width/height (fallback size), upload (false hides it) */
  function draw(canvas, points, o) {
    o = o || {};
    var g = canvas.getContext && canvas.getContext('2d');
    if (!g) return;
    var windowMs = o.windowMs || 180000, end = o.end || Date.now(), start = end - windowMs;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || o.width || 320, h = canvas.clientHeight || o.height || 80;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    var pts = (points || []).filter(function (p) { return p[0] >= start - 1000 && p[0] <= end + 1000; });
    canvas.dataset.points = pts.length;
    var padL = o.axis ? 58 : 6, padR = 8, padT = o.axis ? 10 : 14, padB = o.axis ? 18 : 3;
    var cw = w - padL - padR, ch = h - padT - padB;
    var showUp = o.upload !== false;
    var max = 0;
    pts.forEach(function (p) { max = Math.max(max, p[1], showUp ? p[2] : 0); });
    max = niceMax(max);
    var X = function (t) { return padL + (t - start) / windowMs * cw; };
    var Y = function (v) { return padT + ch - Math.min(v, max) / max * ch; };
    var grid = css('--bs-border-color', '#dee2e6'), muted = css('--bs-secondary-color', '#6c757d'),
        blue = css('--bs-primary', '#0d6efd'), green = css('--bs-success', '#198754'),
        bg = css('--bs-body-bg', '#fff'), fg = css('--bs-body-color', '#212529');

    g.font = '11px system-ui, -apple-system, "Segoe UI", sans-serif';
    g.textBaseline = 'middle';
    g.lineWidth = 1;
    (o.axis ? [0, 0.5, 1] : [0, 1]).forEach(function (f) {
      var y = Math.round(Y(max * f)) + 0.5;
      g.strokeStyle = grid; g.globalAlpha = f === 0 ? 1 : 0.55;
      g.beginPath(); g.moveTo(padL, y); g.lineTo(w - padR, y); g.stroke();
      g.globalAlpha = 1;
      if (o.axis) { g.fillStyle = muted; g.textAlign = 'right'; g.fillText(P.fmtSpeed(max * f), padL - 7, Math.min(Math.max(y, 8), h - padB - 2)); }
    });
    g.fillStyle = muted;
    if (!o.axis) { g.textAlign = 'left'; g.fillText(P.fmtSpeed(max), padL + 2, 6); }
    else { g.textBaseline = 'alphabetic'; g.textAlign = 'left'; g.fillText('-' + span(windowMs), padL, h - 3); g.textAlign = 'right'; g.fillText(o.end && o.end < Date.now() - 4000 ? clock(end) : 'now', w - padR, h - 3); g.textBaseline = 'middle'; }

    if (!pts.length) {
      g.fillStyle = muted; g.textAlign = 'center';
      g.fillText(o.empty || 'No data yet', padL + cw / 2, padT + ch / 2);
      return;
    }

    // split on gaps so a pause in sampling is not drawn as a slope
    var gap = o.gapMs || 6000, segs = [], cur = [];
    pts.forEach(function (p, i) {
      if (i && p[0] - pts[i - 1][0] > gap) { segs.push(cur); cur = []; }
      cur.push(p);
    });
    segs.push(cur);

    function line(idx, color, fill) {
      g.strokeStyle = color; g.fillStyle = color; g.lineWidth = 1.8; g.lineJoin = 'round';
      segs.forEach(function (seg) {
        if (seg.length === 1) { g.beginPath(); g.arc(X(seg[0][0]), Y(seg[0][idx]), 2, 0, 6.2832); g.fill(); return; }
        g.beginPath();
        seg.forEach(function (p, i) { i ? g.lineTo(X(p[0]), Y(p[idx])) : g.moveTo(X(p[0]), Y(p[idx])); });
        g.stroke();
        if (fill) {
          g.lineTo(X(seg[seg.length - 1][0]), Y(0)); g.lineTo(X(seg[0][0]), Y(0)); g.closePath();
          g.globalAlpha = 0.16; g.fill(); g.globalAlpha = 1;
        }
      });
    }
    if (showUp) line(2, green, false);
    line(1, blue, true);

    if (o.hover != null && o.hover >= padL && o.hover <= w - padR) {
      var near = pts[0], best = Infinity;
      pts.forEach(function (p) { var d = Math.abs(X(p[0]) - o.hover); if (d < best) { best = d; near = p; } });
      var x = X(near[0]);
      g.strokeStyle = muted; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ch); g.stroke(); g.setLineDash([]);
      g.fillStyle = blue; g.beginPath(); g.arc(x, Y(near[1]), 3.5, 0, 6.2832); g.fill();
      if (showUp) { g.fillStyle = green; g.beginPath(); g.arc(x, Y(near[2]), 3.5, 0, 6.2832); g.fill(); }
      var lines = [clock(near[0]), '\u2193 ' + P.fmtSpeed(near[1])].concat(showUp ? ['\u2191 ' + P.fmtSpeed(near[2])] : []);
      var bw = 92, bh = 8 + lines.length * 15, bx = x + 10 + bw > w - 2 ? x - 10 - bw : x + 10, by = padT + 2;
      g.fillStyle = bg; g.globalAlpha = 0.95; g.fillRect(bx, by, bw, bh); g.globalAlpha = 1;
      g.strokeStyle = grid; g.strokeRect(bx + 0.5, by + 0.5, bw, bh);
      g.fillStyle = fg; g.textAlign = 'left';
      lines.forEach(function (t, i) { g.fillText(t, bx + 7, by + 12 + i * 15); });
    }
  }

  // getter() -> { points, opts }, evaluated on every redraw
  function redraw(canvas) {
    if (!canvas._getter) return;
    var s = canvas._getter();
    draw(canvas, s.points, Object.assign({}, s.opts, { hover: canvas._hover }));
  }
  function attach(canvas, getter) {
    canvas._getter = getter;
    canvas.addEventListener('mousemove', function (e) { canvas._hover = e.clientX - canvas.getBoundingClientRect().left; redraw(canvas); });
    canvas.addEventListener('mouseleave', function () { canvas._hover = null; redraw(canvas); });
    redraw(canvas);
  }

  P.chart = { draw: draw, attach: attach, redraw: redraw };
})();
