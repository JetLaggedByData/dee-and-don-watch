/* Dee & Don Watch - the monitor page.
   One question: is anything unusual right now?
   Reads the JSON written by pipeline/export.py. Chart.js is the only dependency. */

(function () {
  'use strict';

  /* One source of truth: the palette lives in :root, and the charts and the
     map read it rather than carrying their own copies. Colour used to be
     hardcoded in three JavaScript files, so a theme change meant editing four
     places and trusting them to agree. */
  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  }

  var C = {
    series: token('--series', '#0d6ea3'),
    alert: token('--alert-mark', '#b5651d'),
    neutral: token('--neutral-mark', '#9aa09b'),
    grid: token('--grid', '#e6e2da'),
    axis: token('--axis', '#6d736f'),
    bg: token('--bg', '#f2efe9'),
    surface: token('--surface', '#ffffff'),
    text: token('--text', '#1c1f1d'),
    line: token('--line', '#ddd8d0')
  };

  // Four data-quality flags are one idea to a reader: don't trust this stretch.
  var UNTRUSTED = ['suspect', 'gap', 'flatline', 'spike'];
  var FINDING = ['high_level', 'fast_rise'];

  var chart = null;
  var index = null;
  var details = {};

  function $(id) { return document.getElementById(id); }

  function fmtDateTime(iso) {
    return new Date(iso).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZone: 'UTC'
    }) + ' UTC';
  }

  /* "11 Oct, 17:30" is about 75 px; four of them do not fit the 290 px plot in
     a 25 rem panel. Over more than a couple of days the clock is noise anyway,
     so the axis drops it and keeps the date. The tooltip always shows both. */
  function fmtDayHour(ms, spanMs) {
    var d = new Date(ms);
    if (spanMs !== undefined && spanMs > 2 * 86400000) {
      return d.toLocaleString('en-GB',
        { day: '2-digit', month: 'short', timeZone: 'UTC' });
    }
    // Intraday: the clock, with the date only where the day turns over, so
    // two full "24 Oct, 17:30" labels never have to share 290 px.
    var hm = d.toLocaleString('en-GB',
      { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
    if (spanMs !== undefined && d.getUTCHours() !== 0) return hm;
    return d.toLocaleString('en-GB',
      { day: '2-digit', month: 'short', timeZone: 'UTC' }) + ' ' + hm;
  }

  function recordYears(record) {
    return Math.round((new Date(record.to) - new Date(record.from)) / (365.25 * 864e5));
  }

  function signed(v) {
    if (v === null || v === undefined) return '-';
    var t = Math.abs(v).toFixed(3);
    return v > 0 ? '+' + t : v < 0 ? '-' + t : t;
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  // Never round a percentile up to 100: that reads as "all-time maximum".
  /* Rounding to whole numbers printed 98.8 as "99" beside a gauge genuinely at
     99.0, which made the ranking look arbitrary. Precision follows the tail. */
  function pctText(p) {
    return (p >= 99.9 ? p.toFixed(2) : p >= 90 ? p.toFixed(1) : p.toFixed(0)) + '%';
  }

  /* Position on the list's shared scale.

     A 0-100 linear bar was the wrong instrument: ten of thirteen gauges sit
     between 96% and 99.7%, so almost every bar rendered full and the
     differences that decide the whole page were invisible. This places a gauge
     by how RARE its level is - the exceedance, 100 minus the percentile - on a
     log axis, which spreads the tail where the data actually lives. The 99%
     gridline is the threshold for "unusually high", so crossing it is now
     something you can see rather than something you have to be told. */
  function scalePos(p) {
    var e = Math.min(100, Math.max(0.05, 100 - p));
    var x = (2 - Math.log(e) / Math.LN10) / 3.301 * 100;
    return Math.max(0, Math.min(100, x));
  }

  /* ---------------- what is flagged ---------------- */

  function flagsOf(station) {
    var l = station.latest;
    var out = [];
    if (l.is_fast_rise) out.push('rising unusually fast');
    if (l.is_high_level) out.push('unusually high');
    if (l.is_suspect) out.push('marked Suspect by SEPA');
    if (l.is_gap) out.push('data gap');
    if (l.is_flatline) out.push('sensor not moving');
    if (l.is_spike) out.push('possible spike');
    return out;
  }

  function renderBanner() {
    var banner = $('staleBanner');
    var limit = index.stale_after_hours;
    var age = (Date.now() - new Date(index.generated_utc).getTime()) / 3600000;
    var stale = index.stations.filter(function (s) { return s.stale; });
    var msg = [];
    if (age > limit) {
      msg.push('Last updated <strong>' + age.toFixed(1) + ' hours</strong> ago, longer than the ' +
        limit + '-hour target.');
    }
    if (stale.length) {
      msg.push('No recent data from <strong>' +
        stale.map(function (s) { return s.name; }).join(', ') + '</strong>.');
    }
    if (!msg.length) return;
    banner.innerHTML = msg.join(' ') + ' Readings may be out of date.';
    banner.hidden = false;
  }

  function renderAnswer(flagged) {
    var line = $('answerLine');
    var total = index.stations.length;

    if (!flagged.length) {
      line.className = 'answer-line calm';
      line.innerHTML = 'Nothing unusual on the Dee or Don. <em>All ' + total +
        ' gauges normal.</em>';
      $('answerSub').textContent =
        'Each gauge is judged against its own ' + recordYears(index.stations[0].record) +
        '-year record. A quiet page is not an all-clear - this is not a warning service.';
      return;
    }

    var rising = flagged.filter(function (s) { return s.latest.is_fast_rise; }).length;
    var high = flagged.filter(function (s) { return s.latest.is_high_level; }).length;
    var parts = [];
    if (high) parts.push(plural(high, 'gauge is', 'gauges are') + ' unusually high');
    if (rising) parts.push(plural(rising, 'is', 'are') + ' rising unusually fast');

    line.className = 'answer-line';
    line.innerHTML = '<em>' + flagged.length + ' of ' + total + ' gauges</em> ' +
      (flagged.length === 1 ? 'is' : 'are') + ' showing something unusual.';
    $('answerSub').textContent = parts.join(', ') +
      ' - measured against each gauge\'s own ' + recordYears(index.stations[0].record) +
      '-year record. This describes what has already been recorded. It is not a warning.';
  }

  /* A count with no direction is half an answer: "4 of 13" reads the same
     whether things are building or easing. */
  function renderTrend() {
    var h = index.flagged_history;
    var el = $('trend');
    if (!h || !h.counts || h.counts.length < 25) { el.hidden = true; return; }

    var c = h.counts;
    var now = c[c.length - 1], before = c[c.length - 25];
    var text = now === before
      ? 'Unchanged from this time yesterday'
      : (now > before ? 'Up from ' : 'Down from ') +
        (before === 0 ? 'none' : before) + ' at this time yesterday';

    var W = 148, H = 24, max = Math.max(1, Math.max.apply(null, c));
    var pts = c.map(function (v, i) {
      return [(i / (c.length - 1)) * W, H - (v / max) * (H - 4) - 2];
    });
    var line = pts.map(function (p, i) {
      return (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1);
    }).join('');

    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('class', 'spark');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label',
      'Gauges flagged over the last ' + Math.round(c.length / 24) +
      ' days, highest ' + max + ', now ' + now + ' of ' + h.of);
    svg.innerHTML =
      '<path class="spark-area" d="' + line + 'L' + W + ',' + H + 'L0,' + H + 'Z"/>' +
      '<path class="spark-line" d="' + line + '"/>' +
      '<circle class="spark-now" cx="' + W + '" cy="' +
        pts[pts.length - 1][1].toFixed(1) + '" r="2.6"/>';

    el.hidden = false;
    el.textContent = '';
    el.appendChild(svg);
    el.appendChild(document.createTextNode(text));
  }

  /* ---------------- gauge list ---------------- */

  function gaugeHTML(station) {
    var l = station.latest;
    var flags = flagsOf(station);
    var pct = l.level_pct === null ? 0 : l.level_pct;
    var noisy = station.flag_rates.spike >= 3;
    var x = scalePos(pct);

    return '' +
      '<span class="g-name">' + station.name +
        '<small>' + station.river + ' | ' + station.km_to_aberdeen + ' km</small></span>' +
      '<span class="g-val">' + (l.value === null ? '-' : l.value.toFixed(3)) +
        '<small>m</small></span>' +
      '<span class="g-track" role="img" aria-label="Higher than ' + pctText(pct) +
        ' of its own record">' +
        '<i class="fill" style="width:' + x.toFixed(1) + '%"></i>' +
        '<i class="pin" style="left:' + x.toFixed(1) + '%"></i></span>' +
      '<span class="g-pct">' + pctText(pct) + '</span>' +
      '<span class="g-roc mono">' + signed(l.roc_1h) + ' m/hr</span>' +
      '<span class="g-flag">' +
        (flags.length ? '<span class="badge">' + flags[0] + '</span>' : '') +
        (flags.length > 1 ? '<span class="more">+' + (flags.length - 1) + '</span>' : '') +
      '</span>' +
      (noisy
        ? '<span class="caveat">Noisy gauge: ' + station.flag_rates.spike.toFixed(1) +
          '% of readings flag as possible spikes, against about 1% elsewhere.</span>'
        : '');
  }

  function renderGauges(flagged, calm) {
    var list = $('gaugeList');

    // The scale, stated once above the rows rather than implied by thirteen
    // anonymous bars.
    var scale = document.createElement('p');
    scale.className = 'g-scale';
    scale.setAttribute('aria-hidden', 'true');
    scale.innerHTML =
      '<span style="left:9.1%">50%</span>' +
      '<span style="left:30.3%">90%</span>' +
      '<span class="thresh" style="left:60.6%">99% unusual</span>' +
      '<span style="left:90.9%">99.9%</span>';
    list.appendChild(scale);

    // Flagged gauges sort first, which made the percentile column read as a
    // broken sort (99.7, 99.6, 99.2, 86, 99...). The jump is the grouping, so
    // the grouping is now visible.
    [[flagged, 'Flagged'], [calm, 'Nothing unusual']].forEach(function (group) {
      if (!group[0].length) return;
      var head = document.createElement('h3');
      head.className = 'g-group';
      head.textContent = group[1] + ' | ' + group[0].length;
      list.appendChild(head);

      group[0].forEach(function (station) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'gauge' + (flagsOf(station).length ? ' flagged' : '');
        b.setAttribute('aria-pressed', 'false');
        b.dataset.slug = station.slug;
        b.innerHTML = gaugeHTML(station);
        b.addEventListener('click', function () { select(station.slug); });
        list.appendChild(b);
      });
    });
  }

  /* ---------------- the river ---------------- */

  var geo = null;
  // One shared axis width, so the hyetograph and hydrograph line up exactly.
  var AXIS_W = 46;
  /* Share of the axis reserved above the data for the rain band. Enough that
     the bars never reach the level line, and no more: at 0.5 the data filled
     only 57% of the panel on a typical 24-hour window, which read as the
     chart being broken rather than as breathing room. */
  var RAIN_BAND = 0.18;
  // Chart.js reserves horizontal room for an edge tick label. The level chart
  // has x labels and the rainfall chart does not, so without an identical
  // explicit padding the two plot areas end at different x and the shared time
  // axis stops being shared.
  // Only what the inner-aligned edge labels need. It was 40, reserved for a
  // tick label hanging past the axis, which left the plot stopping well
  // short of the panel edge.
  var PLOT_PAD = { left: 0, right: 6, top: 2, bottom: 0 };
  var forecast = null;
  /* Up to 14 days comes from the station's 15-minute file; longer ranges come
     from its daily history, because ten years at 15 minutes is 350,000 points
     per gauge. */
  var RANGES = [
    { id: '24h', label: '24 h', days: 1 },
    { id: '7d', label: '7 d', days: 7 },
    { id: '14d', label: '14 d', days: 14 },
    { id: '1mo', label: '1 mo', days: 30 },
    { id: '6mo', label: '6 mo', days: 182 },
    { id: '1y', label: '1 y', days: 365 },
    { id: '5y', label: '5 y', days: 1825 },
    { id: '10y', label: '10 y', days: 3650 }
  ];
  var FULL_DETAIL_DAYS = 14;
  // 14 d, the longest range still served from the 15-minute file. A month was
  // the instinct, but it sits past FULL_DETAIL_DAYS, so the view everyone
  // landed on was the degraded one: a daily envelope with no projection and no
  // quality ribbon. The default should show the most the page can show.
  var rangeIndex = 2;
  var customFrom = null;   // an explicit from/to overrides the preset range
  var customTo = null;
  var histories = {};
  var order = [];     // the list's own order, for stepping inside the sheet
  var current = null;

  function renderRiver() {
    if (!window.DDWRiver) return;
    var host = $('riverMap');
    host.innerHTML = window.DDWRiver.build(index, geo);
    if (current) {
      Array.prototype.forEach.call(host.querySelectorAll('.gauge-mark'), function (g) {
        g.setAttribute('aria-current', String(g.dataset.slug === current));
      });
    }
    var MW = token('--map-water', '#2f7fb0');
    var MS = token('--map-spate', '#b5651d');
    function swatch(w, colour) {
      return '<svg width="42" height="12" aria-hidden="true">' +
               '<path d="M0,6 H42" stroke="' + colour + '" stroke-width="' + w +
        '" fill="none"/></svg>';
    }
    $('riverKey').innerHTML =
      '<ul class="maplegend">' +
        '<li>' + swatch(2.2, MW) + 'Low in its own record</li>' +
        '<li>' + swatch(5, MW) + 'Middling</li>' +
        '<li>' + swatch(10, MW) + 'Near its record high</li>' +
        '<li>' + swatch(10, MS) + 'Something flagged</li>' +
      '</ul>' +
      '<details><summary>What the widths mean</summary><p>' +
        'Width = level within that gauge\'s <strong>own ten-year record</strong>, so ' +
        'widths are not comparable between gauges. Both rivers run east into Aberdeen.' +
      '</p></details>';

    $('statPill').textContent =
      index.stations.length + ' gauges | 15-minute readings | SEPA, provisional';

    Array.prototype.forEach.call(host.querySelectorAll('.gauge-mark'), function (g) {
      g.addEventListener('click', function () { select(g.dataset.slug); });
      g.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(g.dataset.slug); }
      });
    });
  }

  /* ---------------- chart ---------------- */

  /* Flagged and untrusted stretches as a ribbon along the bottom, not as
     full-height washes over the series.

     Washes were drowning the chart they annotate: Garthdee flags about 6% of
     its readings, so a fortnight of it rendered as dozens of grey stripes and
     the level line was barely findable among them. The information is the
     same - these spans, at these times - but it now sits beneath the data
     instead of on top of it. */
  var RIBBON_H = 7;

  var bandsPlugin = {
    id: 'bands',
    beforeDatasetsDraw: function (c, args, opts) {
      if (!opts || !opts.spans || !opts.times) return;
      var ctx = c.ctx, x = c.scales.x, area = c.chartArea;
      var top = area.bottom - RIBBON_H;
      ctx.save();

      // A faint rule so the ribbon reads as a track even where it is empty.
      ctx.fillStyle = 'rgba(125, 133, 138, 0.14)';
      ctx.fillRect(area.left, top, area.right - area.left, RIBBON_H);

      [['rgba(125, 133, 138, 0.75)', UNTRUSTED],
       ['rgba(200, 144, 66, 0.95)', FINDING]].forEach(function (g) {
        ctx.fillStyle = g[0];
        g[1].forEach(function (name) {
          (opts.spans[name] || []).forEach(function (p) {
            var from = x.getPixelForValue(opts.times[p[0]]);
            var to = x.getPixelForValue(opts.times[Math.min(p[1], opts.times.length - 1)]);
            if (to < area.left || from > area.right) return;
            from = Math.max(from, area.left);
            to = Math.min(to, area.right);
            ctx.fillRect(from, top, Math.max(to - from, 2), RIBBON_H);
          });
        });
      });
      ctx.restore();
    }
  };

  function countSpans(detail, names) {
    return names.reduce(function (n, k) { return n + (detail.spans[k] || []).length; }, 0);
  }


  /* ---------------- rainfall ---------------- */

  /* Rain and level share one plot area again: bars hanging from the top into
     their own band, the level line below, one time axis.

     This is still two scales, which is worth saying plainly. What keeps it
     honest is that the bars never reach the line, so you read WHEN rain fell
     against when the river rose rather than one height against the other -
     and the rain scale is not drawn as a second axis, because that is what
     made one chart look like two. The peak is in the caption; exact values
     are on hover. */
  function rainSeries(station, series, from, to, perDay) {
    series = (series || []).filter(function (p) {
      return p.y !== null && p.y !== undefined && !isNaN(p.y) &&
             p.x >= from && p.x <= to;
    });
    if (!series.length) return { bars: [], max: 0, text: '' };

    var peak = series.reduce(function (a, p) { return Math.max(a, p.y); }, 0) || 1;
    var unit = perDay ? ' mm/day' : ' mm/hr';
    // The text is returned, not written here. The caller owns the caption: the
    // long view has its own "not every reading" warning to add, and writing
    // from in here overwrote it three lines after it was set.
    return {
      bars: series, max: peak, unit: unit,
      text: '<strong>Rainfall</strong> over the ' +
        (station.catchment || station.river) + ' catchment | ' +
        (perDay ? 'daily totals' : 'hourly') + ', bars peak at ' +
        peak.toFixed(perDay ? 0 : 1) + unit
    };
  }

  /* The y axis fits what is actually on screen.

     This used to filter the points by hand against firstX/lastX and hand the
     result to Chart.js as an explicit min/max. That duplicated Chart.js's own
     windowing and disagreed with it: on a 24-hour view of Garthdee the axis
     was fitted down to 1.03 m while nothing below 2.2 m was drawn, so the line
     sat squashed in the top fifth of the panel.

     `afterDataLimits` runs on the limits of the data Chart.js is really
     plotting for the current x range, so there is nothing left to get out of
     step. On top of that it only has to do two things: keep the "unusually
     high" threshold in frame, so a gauge below it still shows how far below;
     and reserve a band at the top when rain shares the plot, so the bars never
     reach the line. */
  function fitAxis(threshold, headroom) {
    return function (scale) {
      var lo = scale.min, hi = scale.max;
      if (!isFinite(lo) || !isFinite(hi)) return;
      if (threshold !== null && threshold !== undefined && !isNaN(threshold)) {
        lo = Math.min(lo, threshold);
        hi = Math.max(hi, threshold);
      }
      var span = hi - lo || 0.1;
      scale.min = lo - span * 0.06;
      scale.max = hi + span * (0.06 + (headroom || 0));
    };
  }

  /* The daily envelope: minimum to maximum each day, with the mean through
     it. The band is the honest part - a mean alone would hide the short sharp
     rises this project exists to notice. */
  function drawHistory(station, history, win) {
    var start = new Date(history.from + 'T00:00:00Z').getTime();
    var n = history.mid.length;
    var from = Math.max(0, Math.floor((win.from - start) / DAY));
    var n2 = Math.min(n, Math.ceil((win.to - start) / DAY) + 1);

    var lo = [], hi = [], mid = [], rainBars = [];
    var rainAvailable = !!history.rain_mm;
    for (var i = from; i < n2; i++) {
      var x = start + i * DAY;
      lo.push({ x: x, y: history.lo[i] });
      hi.push({ x: x, y: history.hi[i] });
      mid.push({ x: x, y: history.mid[i] });
      if (rainAvailable) rainBars.push({ x: x, y: history.rain_mm[i] });
    }
    // A window before the start of the record leaves nothing to draw.
    if (!mid.length) { clearChart('No readings in this range.'); return; }

    var threshold = station.thresholds && station.thresholds.high_level_m;

    $('legendProjected').hidden = true;
    $('projNote').hidden = true;

    var rain = rainSeries(station, rainBars, mid[0].x, mid[mid.length - 1].x, true);
    rainBars = rain.bars;
    $('rainLabel').hidden = false;
    $('rainLabel').innerHTML = (rain.text ? rain.text + '<br>' : '') +
      '<strong>' + spanLabel(win) + '</strong> | daily range and mean, not every ' +
      'reading. Quality flags and the projection are only drawn at 14 days or less.';
    $('rainUnit').textContent = 'Rainfall (mm/day)';

    var canvas = $('chart');
    canvas.setAttribute('aria-label',
      'Daily minimum, mean and maximum level at ' + station.name + ', ' +
      spanLabel(win) + '.');

    var longSpan = (mid[mid.length - 1].x - mid[0].x) > 400 * DAY;

    if (chart) chart.destroy();
    chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        datasets: ([
          rainBars.length ? {
            type: 'bar', label: 'Rainfall', data: rainBars, yAxisID: 'rain',
            backgroundColor: 'rgba(98, 195, 234, 0.42)',
            borderWidth: 0, barThickness: 'flex', order: 5
          } : null,
          { label: 'Daily high', data: hi, yAxisID: 'y', borderWidth: 0, pointRadius: 0,
            fill: '+1', backgroundColor: 'rgba(44, 158, 211, 0.13)', order: 3 },
          { label: '_dailyLow', data: lo, yAxisID: 'y', borderWidth: 0, pointRadius: 0, order: 3 },
          { label: 'Daily mean', data: mid, yAxisID: 'y', borderColor: C.series, borderWidth: 1.4,
            pointRadius: 0, tension: 0.1, order: 2 },
          { label: 'Unusually high above', yAxisID: 'y',
            data: (threshold === null || threshold === undefined) ? []
              : [{ x: mid[0].x, y: threshold },
                 { x: mid[mid.length - 1].x, y: threshold }],
            borderColor: C.alert, borderWidth: 1.5, borderDash: [5, 5],
            pointRadius: 0, order: 1 }
        ]).filter(Boolean)
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: PLOT_PAD },
        animation: false,
        interaction: { mode: 'index', axis: 'x', intersect: false },
        scales: {
          x: {
            type: 'linear',
            min: mid[0].x, max: mid[mid.length - 1].x,
            // Ticks spaced evenly across the DATA, not at round numbers in
            // epoch milliseconds. Left to itself the scale picked 1.4e12 and
            // friends, which are real dates - 13 May 2014 - just not ones in
            // this record, which starts in 2016.
            afterBuildTicks: function (scale) {
              var a = scale.min, b = scale.max, out = [];
              for (var i = 0; i <= 3; i++) out.push({ value: a + (b - a) * i / 3 });
              scale.ticks = out;
            },
            ticks: { color: C.axis, includeBounds: false, align: 'inner',
              font: { family: 'JetBrains Mono, monospace', size: 10 },
              callback: function (v) { return fmtDay(v, longSpan); } },
            grid: { color: C.grid, drawTicks: false },
            border: { color: C.grid }
          },
          rain: {
            // Reversed, so a bar hangs DOWN from the top into a band of its
            // own. Not drawn as an axis - the peak is in the caption and the
            // exact value is on hover.
            position: 'right', reverse: true, beginAtZero: true,
            max: rain.max ? rain.max * 5 : 1,
            display: false,
            afterFit: function (scale) { scale.width = 0; },
            grid: { display: false },
            border: { display: false }
          },
          y: {
            afterFit: function (scale) { scale.width = AXIS_W; },
            // 'ticks' (the default) stretches the axis out to the next round
            // tick, which turned a -0.93..3.5 m year into a -2..6 m axis and
            // left most of the panel empty. 'data' ends the axis where
            // fitAxis says it should.
            bounds: 'data',
            afterDataLimits: fitAxis(threshold, rainBars.length ? RAIN_BAND : 0),
            ticks: { color: C.axis, font: { family: 'JetBrains Mono, monospace', size: 10 },
              callback: function (v) { return v.toFixed(2) + ' m'; } },
            grid: { color: C.grid, drawTicks: false },
            border: { display: false }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: C.surface, borderColor: C.line, borderWidth: 1,
            titleColor: C.text, bodyColor: C.text, padding: 9, displayColors: false,
            titleFont: { family: 'JetBrains Mono, monospace', size: 11 },
            bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
            callbacks: {
              title: function (i) { return fmtDay(i[0].parsed.x, true); },
              label: function (i) {
                // The lower edge exists only to close the fill.
                if (i.dataset.label === '_dailyLow') return null;
                if (i.dataset.label === 'Rainfall') {
                  return 'Rainfall: ' + i.parsed.y.toFixed(1) + (rain.unit || ' mm/day');
                }
                return i.dataset.label + ': ' + i.parsed.y.toFixed(3) + ' m';
              }
            }
          }
        }
      }
    });
  }

  /* Without this a failed or empty draw left the PREVIOUS gauge's chart on
     screen under the new gauge's name. */
  function clearChart(message) {
    if (chart) { chart.destroy(); chart = null; }
    $('legendProjected').hidden = true;
    $('projNote').hidden = true;
    $('rainLabel').hidden = false;
    $('rainLabel').textContent = message;
  }

  function withHistory(slug, then) {
    if (histories[slug]) { then(histories[slug]); return; }
    fetch('data/' + slug + '_history.json')
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (h) {
        histories[slug] = h;
        // The reader may have moved on while this loaded.
        if (slug === current) then(h);
      })
      .catch(function () {
        if (slug === current) clearChart('Could not load the long-range history.');
      });
  }

  /* The year is only worth the space when the window spans one. In a 25 rem
     panel "25 Sep 26" four times over collides; "25 Sep" does not. */
  function fmtDay(ms, withYear) {
    var d = new Date(ms);
    return d.getUTCDate() + ' ' +
      ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()] +
      (withYear ? ' ' + String(d.getUTCFullYear()).slice(2) : '');
  }

  var DAY = 86400000;

  /* The window the chart is showing, and which file can serve it. */
  function windowFor(detail) {
    var first = new Date(detail.t[0]).getTime();
    var last = new Date(detail.t[detail.t.length - 1]).getTime();
    var from, to;

    if (customFrom && customTo) {
      from = customFrom;
      to = customTo;
    } else {
      to = last;
      from = to - RANGES[rangeIndex].days * DAY;
    }

    // The 15-minute file only holds the last fortnight. Anything older, or
    // longer than it, has to come from the daily history.
    var useDetail = from >= first - DAY && to <= last + DAY &&
                    (to - from) <= (FULL_DETAIL_DAYS + 0.5) * DAY;
    return { from: from, to: to, useDetail: useDetail, latest: last };
  }

  function spanLabel(win) {
    if (!customFrom || !customTo) {
      return RANGES[rangeIndex].label;
    }
    return fmtDay(win.from) + ' to ' + fmtDay(win.to);
  }

  function syncControls() {
    var custom = !!(customFrom && customTo);
    if (custom) {
      $('customFrom').value = new Date(customFrom).toISOString().slice(0, 10);
      $('customTo').value = new Date(customTo).toISOString().slice(0, 10);
    }
    Array.prototype.forEach.call(
      $('rangeToggle').querySelectorAll('button'), function (b) {
        b.setAttribute('aria-pressed', String(
          !custom && parseInt(b.dataset.i, 10) === rangeIndex));
      });
  }

  function drawChart(station, detail) {
    var win = windowFor(detail);
    if (!win.useDetail) {
      withHistory(station.slug, function (h) { drawHistory(station, h, win); });
      return;
    }

    var times = detail.t.map(function (iso) { return new Date(iso).getTime(); });
    var points = times.map(function (t, i) { return { x: t, y: detail.v[i] }; });
    var threshold = detail.thresholds.high_level_m;
    var finite = detail.v.filter(Number.isFinite);

    var canvas = $('chart');
    canvas.setAttribute('aria-label',
      'Line chart of river level at ' + station.name + ' over ' + detail.window_days +
      ' days to ' + fmtDateTime(detail.t[detail.t.length - 1]) + '. Levels from ' +
      Math.min.apply(null, finite).toFixed(3) + ' to ' + Math.max.apply(null, finite).toFixed(3) +
      ' metres. ' + countSpans(detail, FINDING) + ' flagged periods, ' +
      countSpans(detail, UNTRUSTED) + ' untrusted stretches. Same data in the table view.');

    if (chart) chart.destroy();

    /* Projection: only where there is a gauge upstream to route from, and only
       as far as the measured skill holds. The band is the model\'s own test
       error, so the drawn uncertainty is measured rather than decorative.

       Computed before the rainfall chart, because both charts share an x-range
       and that range has to include the projection. */
    var fc = forecast && forecast.reaches && forecast.reaches[station.slug];
    var proj = [], bandHi = [], bandLo = [];
    if (fc) {
      /* The dashed line starts where the measurements stop, not where the
         projection was issued.

         A projection issued at 16:15 still has its first hours on file by
         17:30 - the gauge kept reporting. Drawing it from the issue point put
         a dashed line and a solid line over the same hours, disagreeing with
         each other, which is not a forecast of anything. Hours that have since
         been measured are dropped and the remainder is joined to the last real
         reading, so the chart reads as one line that turns dashed where
         knowledge runs out.

         The projected values themselves are untouched - nothing is shifted to
         meet the reading, only the already-overtaken part is left off. */
      var anchor = null;
      for (var n = detail.v.length - 1; n >= 0 && !anchor; n--) {
        if (detail.v[n] !== null && !isNaN(detail.v[n])) {
          anchor = { x: times[n], y: detail.v[n] };
        }
      }
      if (anchor) {
        proj.push(anchor);
        bandHi.push(anchor);
        bandLo.push(anchor);
        fc.points.forEach(function (p) {
          var x = new Date(p.t).getTime(), b = p.band_m || 0;
          if (x <= anchor.x) return;          // already measured
          proj.push({ x: x, y: p.level_m });
          bandHi.push({ x: x, y: p.level_m + b });
          bandLo.push({ x: x, y: p.level_m - b });
        });
        if (proj.length < 2) { proj = []; bandHi = []; bandLo = []; }
      }
    }
    /* A projection is only shown where it can be honest about the present.

       Two ways it cannot be. The window may not reach the present at all - you
       are looking at last March. Or the projection may be older than the
       readings: routing needs EVERY upstream gauge, so one gauge going quiet
       freezes it while the level trace carries on. Issued at 10:15 against a
       17:30 reading, it starts mid-chart and below the line, which reads as a
       drawing fault rather than as missing data.

       Neither case is fixed by sliding the dashed line up to the last reading:
       that would move a modelled curve to a point it was not computed from. */
    // Three hours, not one. One was the instinct and it was wrong the first
    // time it met real data: Woodend went quiet, Garthdee fell 1.25 h behind
    // and lost its projection entirely - the gauge nearest Aberdeen, over a
    // gap that still left eleven of its twelve hours in the future. At three
    // hours a projection still has most of its horizon ahead of the reader.
    var STALE_PROJECTION_MS = 3 * 60 * 60 * 1000;
    var atNow = win.to >= win.latest - DAY;
    var behind = fc
      ? win.latest - new Date(fc.issued_t).getTime()
      : 0;
    var heldBack = !!fc && behind > STALE_PROJECTION_MS;
    if (!atNow || heldBack) { proj = []; bandHi = []; bandLo = []; fc = null; }
    var lastX = proj.length ? proj[proj.length - 1].x
      : Math.min(times[times.length - 1], win.to);
    var firstX = Math.max(times[0], win.from);

    var rain = rainSeries(station, ((detail.rain && detail.rain.t) || []).map(
      function (iso, n) {
        return { x: new Date(iso).getTime(), y: detail.rain.mm[n] };
      }), firstX, lastX, false);
    var rainBars = rain.bars;
    $('rainLabel').hidden = !rain.text;
    $('rainLabel').innerHTML = rain.text;

    $('rainUnit').textContent = 'Rainfall (mm/hr)';
    $('legendProjected').hidden = !fc;
    $('projNote').hidden = !fc && !heldBack;
    if (heldBack) {
      // Say why there is no dashed line, rather than leaving a gap the reader
      // has to explain to themselves.
      $('projNote').innerHTML =
        'No projection right now: it is ' + (behind / 3600000).toFixed(1) +
        ' h behind the readings because a gauge upstream has stopped ' +
        'reporting, and routing needs all of them. It returns when that gauge ' +
        'does. The levels above are measured and unaffected.';
    } else if (fc) {
      $('projNote').innerHTML =
        'The dashed line routes water <strong>already measured</strong> at ' +
        fc.upstream.join(', ') + ' down to here. It uses no weather forecast, and ' +
        'stops at 12 h because past this catchment\'s ' + fc.travel_time_hours +
        ' h travel time the water has not entered the river yet. The band is ' +
        'the model\'s own error on data it was not trained on. ' +
        '<strong>This is not a flood warning</strong> - for warnings see ' +
        '<a href="https://www.sepa.org.uk/environment/water/flooding/" rel="noopener">SEPA</a>' +
        ' and the <a href="https://www.metoffice.gov.uk/weather/warnings-and-advice/' +
        'uk-warnings" rel="noopener">Met Office</a>.';
    }

    chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        datasets: ([
          rainBars.length ? {
            type: 'bar', label: 'Rainfall', data: rainBars, yAxisID: 'rain',
            backgroundColor: 'rgba(98, 195, 234, 0.42)',
            borderWidth: 0, barThickness: 'flex', order: 5
          } : null,
          { label: 'Level', data: points, yAxisID: 'y', borderColor: C.series, borderWidth: 2,
            pointRadius: 0, pointHoverRadius: 0, tension: 0.1, spanGaps: false, order: 2 },
          { label: 'Unusually high above', yAxisID: 'y',
            data: (threshold === null || threshold === undefined) ? []
              : [{ x: firstX, y: threshold }, { x: lastX, y: threshold }],
            borderColor: C.alert, borderWidth: 1.5, borderDash: [5, 5],
            pointRadius: 0, order: 1 },
          { label: 'Likely range', data: bandHi, yAxisID: 'y', borderWidth: 0,
            pointRadius: 0, fill: '+1',
            backgroundColor: 'rgba(44, 158, 211, 0.13)', order: 4 },
          { label: '_bandLo', data: bandLo, yAxisID: 'y', borderWidth: 0,
            pointRadius: 0, order: 4 },
          { label: 'Projected from water upstream', data: proj, yAxisID: 'y',
            borderColor: C.series, borderWidth: 2, borderDash: [4, 4],
            pointRadius: 0, pointHoverRadius: 0, tension: 0.1, order: 3 }
        ]).filter(Boolean)
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: PLOT_PAD },
        animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? false : { duration: 250 },
        interaction: { mode: 'index', axis: 'x', intersect: false },
        scales: {
          // Linear axis over epoch ms - no date-adapter dependency.
          x: {
            type: 'linear', min: firstX, max: lastX,
            ticks: { maxTicksLimit: 4, align: 'inner', color: C.axis,
              font: { family: 'JetBrains Mono, monospace', size: 10 },
              callback: function (v) { return fmtDayHour(v, lastX - firstX); } },
            grid: { color: C.grid, drawTicks: false },
            border: { color: C.grid }
          },
          // `rain` is declared AFTER `y` on purpose. A dataset with no
          // yAxisID falls back to the first y-type scale in this object, and
          // this one is reversed - every level dataset now names 'y'
          // explicitly as well, but the order is the second line of defence.
          rain: {
            // Reversed, so a bar hangs DOWN from the top, and given several
            // times the headroom it needs so the band never reaches the line.
            // Not drawn as an axis: a second axis stack on the opposite side
            // was what made one chart read as two bolted together. The peak is
            // stated in the caption and exact values are on hover.
            position: 'right', reverse: true, beginAtZero: true,
            max: rain.max ? rain.max * 5 : 1,
            display: false,
            afterFit: function (scale) { scale.width = 0; },
            grid: { display: false },
            border: { display: false }
          },
          y: {
            afterFit: function (scale) { scale.width = AXIS_W; },
            // 'ticks' (the default) stretches the axis out to the next round
            // tick, which turned a -0.93..3.5 m year into a -2..6 m axis and
            // left most of the panel empty. 'data' ends the axis where
            // fitAxis says it should.
            bounds: 'data',
            afterDataLimits: fitAxis(threshold, rainBars.length ? RAIN_BAND : 0),
            // Headroom at the top so the line stays clear of the rain band.
            ticks: { color: C.axis, font: { family: 'JetBrains Mono, monospace', size: 10 },
              callback: function (v) { return v.toFixed(2) + ' m'; } },
            grid: { color: C.grid, drawTicks: false },
            border: { display: false }
          },
        },
        plugins: {
          legend: { display: false },
          bands: { spans: detail.spans, times: times },
          tooltip: {
            backgroundColor: C.surface, borderColor: C.line, borderWidth: 1,
            titleColor: C.text, bodyColor: C.text, padding: 9, displayColors: false,
            titleFont: { family: 'JetBrains Mono, monospace', size: 11 },
            bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
            callbacks: {
              title: function (i) { return fmtDayHour(i[0].parsed.x) + ' UTC'; },
              label: function (i) {
                // The lower band edge exists only to close the fill.
                if (i.dataset.label === '_bandLo') return null;
                // Rain is the other measure sharing this plot, so it has to
                // carry its own unit here - the axis no longer states it.
                if (i.dataset.label === 'Rainfall') {
                  return 'Rainfall: ' + i.parsed.y.toFixed(1) + (rain.unit || ' mm/hr');
                }
                return i.dataset.label + ': ' + i.parsed.y.toFixed(3) + ' m';
              }
            }
          }
        }
      },
      plugins: [bandsPlugin]
    });
  }

  /* ---------------- table twin ---------------- */

  var LABEL = {
    fast_rise: 'unusual rise', high_level: 'unusually high', suspect: 'suspect',
    gap: 'gap', flatline: 'flatline', spike: 'spike'
  };

  function renderSeriesTable(detail) {
    // Daily rainfall totals, so the table twin carries the same story the
    // chart does rather than only half of it.
    var rainByDay = {};
    if (detail.rain && detail.rain.t) {
      detail.rain.t.forEach(function (iso, i) {
        var d = iso.slice(0, 10);
        if (Number.isFinite(detail.rain.mm[i])) {
          rainByDay[d] = (rainByDay[d] || 0) + detail.rain.mm[i];
        }
      });
    }

    var days = {};
    detail.t.forEach(function (iso, i) {
      var d = iso.slice(0, 10);
      if (!days[d]) days[d] = { vals: [], flags: {} };
      if (Number.isFinite(detail.v[i])) days[d].vals.push(detail.v[i]);
    });
    FINDING.concat(UNTRUSTED).forEach(function (name) {
      (detail.spans[name] || []).forEach(function (p) {
        for (var i = p[0]; i <= p[1]; i++) {
          var d = detail.t[i] && detail.t[i].slice(0, 10);
          if (d && days[d]) days[d].flags[name] = true;
        }
      });
    });

    document.querySelector('#seriesTable tbody').innerHTML =
      Object.keys(days).sort().map(function (day) {
        var d = days[day];
        if (!d.vals.length) return '<tr><td>' + day + '</td><td colspan="5">no data</td></tr>';
        var mean = d.vals.reduce(function (a, b) { return a + b; }, 0) / d.vals.length;
        var flags = Object.keys(d.flags).map(function (k) { return LABEL[k]; });
        return '<tr><td>' + day + '</td>' +
          '<td>' + (rainByDay[day] === undefined ? '-' : rainByDay[day].toFixed(1)) + '</td>' +
          '<td>' + Math.min.apply(null, d.vals).toFixed(3) + '</td>' +
          '<td>' + mean.toFixed(3) + '</td>' +
          '<td>' + Math.max.apply(null, d.vals).toFixed(3) + '</td>' +
          '<td class="flagcell">' + (flags.length ? flags.join(', ') : '-') + '</td></tr>';
      }).join('');
  }

  /* What is upstream of this gauge, right now.

     Observational context only. The one routed projection is the dashed line
     on the chart, at the two outlet gauges; this list makes no "expect X here
     by Y" claim of its own. SEPA and the Met Office issue the warnings. */
  function renderUpstream(station) {
    var box = $('upstream');
    var stem = station.river === station.catchment;
    var up = index.stations.filter(function (s) {
      return s.river === station.river && s.km_to_aberdeen > station.km_to_aberdeen;
    }).sort(function (a, b) { return a.km_to_aberdeen - b.km_to_aberdeen; });

    var html = '<h3>Upstream right now</h3>';

    if (up.length) {
      html += '<ul>' + up.map(function (s) {
        var p = s.latest.level_pct;
        var hot = flagsOf(s).length;
        return '<li><span class="u-name">' + s.name + '</span>' +
          '<span class="u-pct' + (hot ? ' hot' : '') + '">' +
            (p === null ? '-' : pctText(p)) + '</span>' +
          '<span class="u-km">' +
            (s.km_to_aberdeen - station.km_to_aberdeen).toFixed(1) + ' km up</span></li>';
      }).join('') + '</ul>';
    } else if (stem) {
      html += '<p>Nothing gauged above this point on the ' + station.river + '.</p>';
    } else {
      html += '<p>The ' + station.river + ' carries one gauge, so there is nothing ' +
              'gauged above this point. It joins the ' + station.catchment + '.</p>';
    }

    html += '<p class="note">Water takes time to travel: on the largest event in ' +
      'the record a wave took <strong>13 h 45 m</strong> to cross the Dee\'s 82 km. ' +
      'That is measured history from one event, not a promise about this one. ' +
      'For warnings see <a href="https://www.sepa.org.uk/environment/water/flooding/" ' +
      'rel="noopener">SEPA</a> and the <a href="https://www.metoffice.gov.uk/weather/' +
      'warnings-and-advice/uk-warnings" rel="noopener">Met Office</a>.</p>';

    box.innerHTML = html;
  }

  /* ---------------- detail sheet ---------------- */

  var lastFocus = null;

  /* Two views of one panel, not a dialog over the page. The map stays visible
     either way, which is the whole point: picking the next gauge is a click on
     the map, not a close-then-reopen. */
  function showDetail() {
    lastFocus = document.activeElement;
    $('viewList').hidden = true;
    $('viewDetail').hidden = false;
    $('panel').scrollTop = 0;
    if (chart) chart.resize();
  }

  /* Expanding moves the one chart into the overlay rather than building a
     second one: one Chart instance, one set of listeners, and no chance of the
     panel copy and the big copy disagreeing about range or view. */
  var expanded = false;

  /* Every gauge, listed. Paging through thirteen with arrows meant holding
     the order in your head; this lets you go straight to the one you want and
     shows which are flagged while you choose. */
  function renderGaugeBar() {
    var bar = $('gaugeBar');
    bar.innerHTML = order.map(function (slug) {
      var s = index.stations.filter(function (x) { return x.slug === slug; })[0];
      if (!s) return '';
      return '<button type="button" data-slug="' + slug + '"' +
        (flagsOf(s).length ? ' class="hot"' : '') +
        ' aria-pressed="false">' + s.name + '</button>';
    }).join('');
    Array.prototype.forEach.call(bar.querySelectorAll('button'), function (b) {
      b.addEventListener('click', function () { select(b.dataset.slug); });
    });
    markGaugeBar();
  }

  function markGaugeBar() {
    Array.prototype.forEach.call(
      $('gaugeBar').querySelectorAll('button'), function (b) {
        b.setAttribute('aria-pressed', String(b.dataset.slug === current));
      });
  }

  function setExpanded(on) {
    expanded = on;
    var zone = $('chartZone');
    $('chartModal').hidden = !on;
    document.body.classList.toggle('modal-open', on);
    $('expandChart').setAttribute('aria-expanded', String(on));
    if (on) $('modalSlot').appendChild(zone);
    // Back into the detail view, ahead of the notes that follow it.
    else $('viewDetail').insertBefore(zone, $('projNote'));
    $('modalTitle').textContent = $('detailName').textContent;
    updateHash();
    if (chart) chart.resize();
    (on ? $('modalClose') : $('expandChart')).focus();
  }

  function showList() {
    if (expanded) setExpanded(false);
    $('viewDetail').hidden = true;
    $('viewList').hidden = false;
    if (window.history && history.replaceState) {
      history.replaceState(null, '', window.location.pathname);
    }
    var back = current && document.querySelector('.gauge[data-slug="' + current + '"]');
    if (back) back.focus();
    else if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function renderRanges() {
    var box = $('rangeToggle');
    box.innerHTML = RANGES.map(function (r, i) {
      return '<button type="button" data-i="' + i + '" aria-pressed="' +
        (i === rangeIndex) + '">' + r.label + '</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('button'), function (b) {
      b.addEventListener('click', function () {
        setRange(parseInt(b.dataset.i, 10));
      });
    });
  }

  function setRange(i) {
    rangeIndex = Math.max(0, Math.min(RANGES.length - 1, i));
    // Choosing a preset drops any explicit dates and returns to the present.
    customFrom = customTo = null;
    syncControls();
    if (current) { select(current, true); updateHash(); }
  }

  function setView(mode) {
    var isChart = mode === 'chart';
    $('chartbox').hidden = !isChart;
    $('seriesTableWrap').hidden = isChart;
    $('viewChart').setAttribute('aria-pressed', String(isChart));
    $('viewTable').setAttribute('aria-pressed', String(!isChart));
    if (isChart && chart) chart.resize();
  }

  /* The sheet covers the list it was opened from, so without this the only
     route to another gauge is to close it first. */
  function updateHash() {
    if (!current || !window.history || !history.replaceState) return;
    history.replaceState(null, '', '#' + current + ':' + RANGES[rangeIndex].id +
      (expanded ? ':big' : ''));
  }

  function step(delta) {
    var i = order.indexOf(current);
    if (i < 0 || !order.length) return;
    select(order[(i + delta + order.length) % order.length]);
  }

  function select(slug, quiet) {
    current = slug;
    var station = index.stations.filter(function (s) { return s.slug === slug; })[0];
    if (!station) return;

    Array.prototype.forEach.call(document.querySelectorAll('.gauge'), function (g) {
      g.setAttribute('aria-pressed', String(g.dataset.slug === slug));
    });
    Array.prototype.forEach.call(document.querySelectorAll('.gauge-mark'), function (g) {
      g.setAttribute('aria-current', String(g.dataset.slug === slug));
    });

    function show(detail) {
      details[slug] = detail;
      // A slow fetch for a gauge the reader has since left must not draw over
      // the one they are now looking at.
      if (slug !== current) return;
      drawChart(station, detail);
      renderSeriesTable(detail);
      if (!quiet) {
        showDetail();
        // replaceState, not location.hash: assigning the hash scrolls the
        // page to the matching id and jumps you out from under the panel.
        updateHash();
      }
      $('detailName').textContent = station.name;
      $('modalTitle').textContent = station.name;
      renderUpstream(station);
      var at = order.indexOf(station.slug);
      $('gaugePos').textContent = at < 0 ? '' : (at + 1) + ' of ' + order.length;
      markGaugeBar();
      $('detailSub').textContent = station.river + ' | unusually high above ' +
        detail.thresholds.high_level_m + ' m | fast rise above ' +
        detail.thresholds.rise_1h_m_per_hr + ' m/hr | both this gauge\'s own percentiles';
    }

    if (details[slug]) { show(details[slug]); return; }

    fetch('data/' + slug + '.json')
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(show)
      .catch(function (e) {
        // Rethrow to the console as well as the page. A silent catch here hid
        // two chart bugs already - an invalid axis option and a use-before-
        // assignment - both of which rendered a blank chart with a clean log.
        console.error('detail render failed', e);
        $('detailSub').textContent = 'Could not load ' + slug + '.json - ' + e.message;
      });
  }

  /* ---------------- boot ---------------- */

  $('prevGauge').addEventListener('click', function () { step(-1); });
  $('nextGauge').addEventListener('click', function () { step(1); });
  $('backToList').addEventListener('click', showList);
  $('expandChart').addEventListener('click', function () { setExpanded(!expanded); });

  function applyCustom() {
    var a = $('customFrom').value, b = $('customTo').value;
    if (!a || !b) return;
    var from = Date.parse(a + 'T00:00:00Z'), to = Date.parse(b + 'T23:59:59Z');
    if (isNaN(from) || isNaN(to) || to <= from) return;
    customFrom = from;
    customTo = to;
    syncControls();
    if (current) select(current, true);
  }
  $('customFrom').addEventListener('change', applyCustom);
  $('customTo').addEventListener('change', applyCustom);
  $('modalClose').addEventListener('click', function () { setExpanded(false); });
  $('chartModal').addEventListener('click', function (e) {
    if (e.target === $('chartModal')) setExpanded(false);
  });
  // aria-modal promises a dialog: Escape closes it and Tab stays inside it.
  document.addEventListener('keydown', function (e) {
    if (!expanded) return;
    if (e.key === 'Escape') { setExpanded(false); return; }
    if (e.key !== 'Tab') return;
    var items = Array.prototype.filter.call(
      $('chartModal').querySelectorAll('button, input, a[href]'),
      function (el) { return !el.disabled && el.offsetParent !== null; });
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  renderRanges();

  // The map's shape follows the window, so it is redrawn when that changes.
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (index) renderRiver(); }, 180);
  });

  $('viewChart').addEventListener('click', function () { setView('chart'); });
  $('viewTable').addEventListener('click', function () { setView('table'); });

  fetch('data/stations.json')
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (data) {
      index = data;
      $('generated').textContent = fmtDateTime(index.generated_utc);
      $('generated').dateTime = index.generated_utc;
      $('attribution').textContent = index.attribution;

      // Worst first: flagged gauges ranked by how high they sit in their own record.
      var flagged = index.stations.filter(function (s) { return flagsOf(s).length; })
        .sort(function (a, b) { return b.latest.level_pct - a.latest.level_pct; });
      var calm = index.stations.filter(function (s) { return !flagsOf(s).length; })
        .sort(function (a, b) { return b.latest.level_pct - a.latest.level_pct; });

      renderBanner();
      renderAnswer(flagged);
      renderTrend();
      renderRiver();
      // Outlines are static and cheap; redraw once they land rather than
      // blocking the whole page on them.
      fetch('data/forecast.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (f) { if (f) { forecast = f; if (current) select(current, true); } })
        .catch(function () { /* the chart simply has no projection */ });
      fetch('geo/catchment.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (g) { if (g) { geo = g; renderRiver(); } })
        .catch(function () { /* the map still works without outlines */ });
      order = flagged.concat(calm).map(function (s) { return s.slug; });
      renderGaugeBar();
      renderGauges(flagged, calm);
      // A link to a gauge opens on that gauge; otherwise pre-render the worst
      // one without opening over the map.
      var hash = decodeURIComponent((window.location.hash || '').slice(1)).split(':');
      var asked = hash[0];
      if (hash[1]) {
        RANGES.forEach(function (r, i) { if (r.id === hash[1]) rangeIndex = i; });
        renderRanges();
      }
      if (asked && order.indexOf(asked) >= 0) {
        select(asked);
        if (hash[2] === 'big') setExpanded(true);
      } else {
        select((flagged[0] || calm[0]).slug, true);
      }
    })
    .catch(function (err) {
      $('answerLine').textContent = 'Could not load the data.';
      $('answerSub').textContent = err.message;
    });
})();
