/* One flood event as space and time at once.

   Each row is one main-stem gauge, placed at its true distance from Aberdeen,
   with its hourly level (scaled within the event) running left to right. Water moving downstream shows
   up as brightness moving down and to the right, so the slope of that diagonal
   is the wave's apparent speed - the thing the method page measures at 5.97
   km/h on the Dee is visible here rather than tabulated.

   Main stem only. The flood-wave work found the gauges form a sequence only
   along a single watercourse; including the tributaries put 8 of 13 out of
   order, because straight-line distance to Aberdeen is not position along a
   river. A tributary has no place on this axis, so it is not given one.

   Nothing is interpolated between rows. The gaps between gauges are gaps: the
   30 km between Mar Lodge and Polhollick is unmeasured, and drawing a smooth
   field across it would be inventing the very thing the chart claims to show.
*/
window.DDWSpaceTime = (function () {
  'use strict';

  /* Sequential ramp, low to high percentile, on the dark surface. Monotonic in
     OKLab lightness (0.241 -> 0.831), which is the check that applies to a
     sequential ramp; the categorical lightness band does not, since this
     encodes magnitude rather than identity. The three darkest steps sit under
     3:1 against the surface, so the view never leans on colour alone: row
     labels are always visible, the legend is numeric, and hover gives values. */
  var RAMP = [[16, 34, 44], [21, 56, 74], [27, 82, 112],
              [42, 123, 166], [70, 166, 212], [138, 212, 240]];

  var W = 1000, GUTTER = 132, RIGHT = 20, ROW_H = 19;
  var rows = [], span = null;   // geometry kept for the hover readout
  var unit = '% of its own record';

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function rgb(pct) {
    var t = Math.max(0, Math.min(100, pct)) / 100 * (RAMP.length - 1);
    var i = Math.min(RAMP.length - 2, Math.floor(t)), f = t - i;
    var a = RAMP[i], b = RAMP[i + 1];
    return [Math.round(a[0] + (b[0] - a[0]) * f),
            Math.round(a[1] + (b[1] - a[1]) * f),
            Math.round(a[2] + (b[2] - a[2]) * f)];
  }

  /* One row of hourly percentile becomes a 1-pixel-tall image, stretched to the
     row's width. Stretching is honest here: the axis is time, which is
     continuous, and the gauge really was at some level between readings. */
  function rowImage(pct) {
    var c = document.createElement('canvas');
    c.width = pct.length; c.height = 1;
    var ctx = c.getContext('2d');
    var img = ctx.createImageData(pct.length, 1);
    for (var i = 0; i < pct.length; i++) {
      var v = pct[i];
      if (v === null || v === undefined || isNaN(v)) {
        img.data[i * 4 + 3] = 0;               // a gap stays a hole
        continue;
      }
      var c3 = rgb(v);
      img.data[i * 4] = c3[0];
      img.data[i * 4 + 1] = c3[1];
      img.data[i * 4 + 2] = c3[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c.toDataURL('image/png');
  }

  var MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* Tick spacing follows the window. A fortnight wants dates every couple of
     days; a 72-hour event wants times, or the axis carries two labels. */
  function axisTicks(start, hours) {
    var dense = hours <= 96;
    var stepMs = (dense ? 12 : 48) * 3600000;
    var ticks = [], d = new Date(start.getTime());
    d.setUTCHours(0, 0, 0, 0);
    while (d.getTime() <= start.getTime() + hours * 3600000) {
      var h = (d.getTime() - start.getTime()) / 3600000;
      if (h >= 0 && h <= hours) {
        var dt = new Date(d.getTime());
        ticks.push({
          h: h,
          label: !dense || dt.getUTCHours() === 0
            ? dt.getUTCDate() + ' ' + MONTH[dt.getUTCMonth()]
            : (dt.getUTCHours() < 10 ? '0' : '') + dt.getUTCHours() + ':00'
        });
      }
      d.setTime(d.getTime() + stepMs);
    }
    return ticks;
  }

  function panel(river, gauges, start, hours, top) {
    var kms = gauges.map(function (g) { return g.km; });
    var kmMax = Math.max.apply(null, kms), kmMin = Math.min.apply(null, kms);
    var innerH = gauges.length * 34;
    var plotW = W - GUTTER - RIGHT;
    var svg = '';

    function yOf(km) {
      var f = (kmMax - km) / ((kmMax - kmMin) || 1);
      return top + f * (innerH - ROW_H);
    }

    svg += '<text x="0" y="' + (top - 12) + '" class="st-river">' + esc(river) + '</text>';

    gauges.forEach(function (g) {
      var y = yOf(g.km);
      rows.push({ name: g.name, km: g.km, pct: g.pct, y: y, h: ROW_H });
      svg += '<image x="' + GUTTER + '" y="' + y.toFixed(1) + '" width="' + plotW +
             '" height="' + ROW_H + '" preserveAspectRatio="none" href="' +
             rowImage(g.pct) + '"/>';
      // One line, not two: Park and Garthdee are 13 km apart, which is under
      // 20 px here, and stacked labels collided.
      svg += '<text x="' + (GUTTER - 10) + '" y="' + (y + 13).toFixed(1) +
             '" text-anchor="end" class="st-name">' + esc(g.name) +
             ' <tspan class="st-km">' + g.km.toFixed(1) + ' km</tspan></text>';
    });

    axisTicks(start, hours).forEach(function (t) {
      var x = GUTTER + (t.h / hours) * plotW;
      svg += '<line class="st-grid" x1="' + x.toFixed(1) + '" y1="' + top +
             '" x2="' + x.toFixed(1) + '" y2="' + (top + innerH) + '"/>';
      svg += '<text x="' + x.toFixed(1) + '" y="' + (top + innerH + 14) +
             '" text-anchor="middle" class="st-tick">' + t.label + '</text>';
    });

    return { svg: svg, height: innerH + 34 };
  }

  function summarise(data) {
    var parts = [];
    Object.keys(data.rivers).forEach(function (river) {
      var gs = data.rivers[river];
      var peak = gs.map(function (g) {
        var top = Math.max.apply(null, g.pct.filter(function (v) {
          return v !== null && !isNaN(v);
        }));
        return g.name + ' reached ' + top.toFixed(0) + unit;
      });
      parts.push(river + ': ' + peak.join('; '));
    });
    var when = data.event_utc
      ? 'the ' + data.window_days + ' days around the ' +
        new Date(data.event_utc).toISOString().slice(0, 10) + ' event'
      : 'the last ' + data.window_days + ' days';
    return 'Space-time view of ' + when + '. ' + parts.join('. ') + '.';
  }

  // One table per river: a table may only have one header row group.
  function table(data) {
    var html = '';
    Object.keys(data.rivers).forEach(function (river) {
      var gs = data.rivers[river];
      html += '<table class="st-table"><caption class="visually-hidden">' +
        'Highest value reached each day by each ' + esc(river) + ' main-stem gauge, ' +
        'as a percentage ' + esc(data.unit || 'of its own record') + '</caption>' +
        '<thead><tr><th scope="col">' + esc(river) + '</th>';
      var days = Math.floor(gs[0].pct.length / 24);
      var start = new Date(data.from);
      for (var d = 0; d < days; d++) {
        var dt = new Date(start.getTime() + d * 86400000);
        html += '<th scope="col">' + dt.getUTCDate() + '</th>';
      }
      html += '</tr></thead><tbody>';
      gs.forEach(function (g) {
        html += '<tr><th scope="row">' + esc(g.name) + '</th>';
        for (var d2 = 0; d2 < days; d2++) {
          var slice = g.pct.slice(d2 * 24, d2 * 24 + 24).filter(function (v) {
            return v !== null && !isNaN(v);
          });
          html += '<td>' + (slice.length
            ? Math.max.apply(null, slice).toFixed(0) : '-') + '</td>';
        }
        html += '</tr>';
      });
      html += '</tbody></table>';
    });
    return html;
  }

  function legend(unitText) {
    var stops = RAMP.map(function (c, i) {
      return '<stop offset="' + (i / (RAMP.length - 1) * 100).toFixed(0) +
             '%" stop-color="rgb(' + c.join(',') + ')"/>';
    }).join('');
    var ticks = [0, 25, 50, 75, 100].map(function (v) {
      return '<text x="' + (10 + v / 100 * 170).toFixed(0) +
             '" y="26" text-anchor="middle" class="st-tick">' + v + '</text>';
    }).join('');
    return '<svg class="st-legend" viewBox="0 0 190 32" role="img" ' +
      'aria-label="Colour scale: 0 to 100 percent ' + esc(unitText) + '">' +
      '<defs><linearGradient id="stRamp">' + stops + '</linearGradient></defs>' +
      '<rect x="10" y="0" width="170" height="10" fill="url(#stRamp)"/>' +
      ticks + '</svg>';
  }

  function build(data) {
    rows = [];
    unit = data.unit ? '% ' + data.unit : '% of its own record';
    var rivers = Object.keys(data.rivers);
    if (!rivers.length) return '';
    var first = data.rivers[rivers[0]][0];
    var start = new Date(data.from);
    var hours = first.pct.length - 1;
    span = { start: start, hours: hours };

    var body = '', top = 26;
    rivers.forEach(function (river) {
      var p = panel(river, data.rivers[river], start, hours, top);
      body += p.svg;
      top += p.height + 30;
    });

    return '<svg viewBox="0 0 ' + W + ' ' + (top - 30) + '" class="st-svg" ' +
      'role="img" aria-label="' + esc(summarise(data)) + '">' + body + '</svg>' +
      '<div class="st-foot">' + legend(data.unit || 'of its own record') +
      '<details class="st-details"><summary>Show as a table</summary>' +
      '<div class="tablewrap">' + table(data) + '</div></details></div>';
  }

  /* An HTML chart should answer "what is that value" without a table. */
  function bind(host) {
    var svg = host.querySelector('.st-svg');
    if (!svg || !span) return;
    var tip = document.createElement('div');
    tip.className = 'st-tip';
    tip.hidden = true;
    host.style.position = 'relative';
    host.appendChild(tip);

    svg.addEventListener('mousemove', function (e) {
      var b = svg.getBoundingClientRect();
      var vx = (e.clientX - b.left) / b.width * W;
      var vy = (e.clientY - b.top) / b.height * (svg.viewBox.baseVal.height || 1);
      var row = null;
      for (var i = 0; i < rows.length; i++) {
        if (vy >= rows[i].y && vy <= rows[i].y + rows[i].h) { row = rows[i]; break; }
      }
      var plotW = W - GUTTER - RIGHT;
      var f = (vx - GUTTER) / plotW;
      if (!row || f < 0 || f > 1) { tip.hidden = true; return; }

      var k = Math.round(f * span.hours);
      var v = row.pct[k];
      var at = new Date(span.start.getTime() + k * 3600000);
      tip.innerHTML = '<strong>' + esc(row.name) + '</strong> ' + row.km.toFixed(1) + ' km<br>' +
        (v === null || isNaN(v) ? 'no reading' : v.toFixed(1) + unit) +
        '<br><span>' + at.toISOString().slice(0, 16).replace('T', ' ') + ' UTC</span>';
      tip.hidden = false;
      tip.style.left = Math.min(host.clientWidth - 150, (e.clientX - b.left) + 12) + 'px';
      tip.style.top = ((e.clientY - b.top) + 12) + 'px';
    });
    svg.addEventListener('mouseleave', function () { tip.hidden = true; });
  }

  return { build: build, bind: bind };
})();
