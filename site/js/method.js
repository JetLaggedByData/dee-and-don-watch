/* Dee & Don Watch - the method page: flood wave, backtest, limitations.
   No Chart.js here; the cascade is plain HTML, which is lighter and more
   accessible than a canvas for nine labelled points. */

(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function fmtDateTime(iso) {
    return new Date(iso).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZone: 'UTC'
    }) + ' UTC';
  }

  function fmtDayHour(ms) {
    return new Date(ms).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC'
    });
  }

  /* ---------------- flood wave ---------------- */

  function riverHTML(river) {
    var span = river.gauges[river.gauges.length - 1].hours_from_first || 1;

    var rows = river.gauges.map(function (g) {
      var pct = Math.min(Math.max((100 * g.hours_from_first) / span, 1.5), 98.5);
      return '<div class="cascade-row">' +
          '<span class="cascade-label">' + g.name +
            '<small>' + g.km_to_aberdeen + ' km | ' + g.peak_m.toFixed(2) + ' m</small></span>' +
          '<span class="cascade-track">' +
            '<span class="cascade-marker" style="left:' + pct + '%">' +
              '<span class="cascade-dot"></span>' +
              '<span class="cascade-time">+' + g.hours_from_first.toFixed(2) + 'h</span>' +
            '</span></span>' +
        '</div>';
    }).join('');

    return '<div class="river">' +
        '<div class="river-head">' +
          '<span class="river-name">' + river.river + '</span>' +
          '<span class="river-stat">' + river.total_hours.toFixed(2) + ' h | ' +
            river.span_km + ' km | ' + river.apparent_kmh + ' km/h apparent | ' +
            (river.monotonic ? 'strictly in order' : 'NOT in order') + '</span>' +
        '</div>' +
        '<div class="cascade">' + rows + '</div>' +
        '<div class="cascade-axis"><span>upstream peak</span><span>+' +
          span.toFixed(1) + 'h</span></div>' +
      '</div>';
  }

  function renderWave(wave) {
    $('waveSub').textContent =
      'The largest event in the record (' + fmtDateTime(wave.event_utc) + '), chosen from ' +
      'the data rather than assumed. Only gauges on the Dee and Don themselves form a ' +
      'sequence - tributary gauges sit on different watercourses and are listed below.';

    $('waveRivers').innerHTML = wave.rivers.map(riverHTML).join('');

    $('waveTributaries').innerHTML = wave.tributaries.map(function (t) {
      return '<div class="chip">' + t.name + ' | ' + t.river +
        '<b>' + t.hours_before_outlet.toFixed(1) + 'h earlier</b></div>';
    }).join('');

    var reaches = [];
    wave.rivers.forEach(function (r) { r.reaches.forEach(function (x) { reaches.push(x); }); });

    document.querySelector('#waveTable tbody').innerHTML = reaches.map(function (r) {
      return '<tr>' +
        '<td>' + r.from + ' to ' + r.to + '</td>' +
        '<td>' + r.km_apart + ' km</td>' +
        '<td>' + r.peak_to_peak_hours.toFixed(2) + ' h</td>' +
        '<td>' + (r.xcorr_hours === null ? '-' : r.xcorr_hours.toFixed(2) + ' h') +
          '<span class="base">r = ' +
          (r.xcorr_correlation === null ? '-' : r.xcorr_correlation.toFixed(3)) + '</span></td>' +
        '<td' + (r.agrees ? '' : ' class="flagcell"') + '>' +
          (r.difference_hours === null ? '-' : r.difference_hours.toFixed(2) + ' h') +
          '<span class="base">' + (r.agrees ? 'consistent' : 'differ') + '</span></td>' +
      '</tr>';
    }).join('');

    var agreed = reaches.filter(function (r) { return r.agrees; }).length;
    $('waveNotes').innerHTML =
      'The two methods agree within ' + wave.agreement_hours + ' h on <strong>' + agreed +
      ' of ' + reaches.length + '</strong> reaches. The downstream <em>ordering</em> is ' +
      'unambiguous; the individual travel times are approximate.';
  }

  /* ---------------- backtest ---------------- */

  function leadCell(flag) {
    if (flag.first_fired === null) {
      return '<span class="miss">MISS</span><span class="base">on ' +
        flag.base_rate_pct.toFixed(2) + '% of record</span>';
    }
    return (flag.censored ? '>=' : '') + flag.lead_hours.toFixed(1) + 'h' +
      '<span class="base">on ' + flag.base_rate_pct.toFixed(2) + '% of record | ' +
      flag.episodes_unexplained + '/' + flag.episodes_total + ' away from a top peak</span>';
  }

  function renderBacktest(backtest, years) {
    $('backtestSub').textContent =
      'The largest high-water event in the ' + years + '-year record: ' +
      fmtDateTime(backtest.event_utc) + ', when all ' + backtest.stations_involved.length +
      ' gauges peaked within ' + backtest.lookback_hours + ' hours of each other. Every lead ' +
      'time is shown beside how much of the record that flag is set for - alone, a lead time ' +
      'means nothing.';

    document.querySelector('#backtestTable tbody').innerHTML =
      backtest.results.map(function (row) {
        if (!row.peak) {
          return '<tr><td>' + row.station + '</td><td colspan="5">No data in the event window</td></tr>';
        }
        return '<tr>' +
          '<td>' + row.station + '</td>' +
          '<td>' + fmtDayHour(new Date(row.peak).getTime()) + '</td>' +
          '<td>' + row.peak_m.toFixed(2) + ' m<span class="base">' +
            row.peak_pct.toFixed(1) + 'th pct</span></td>' +
          '<td>' + leadCell(row.flags.is_fast_rise) + '</td>' +
          '<td>' + leadCell(row.flags.is_fast_rise_high) + '</td>' +
          '<td>' + leadCell(row.flags.is_high_level) + '</td>' +
        '</tr>';
      }).join('');

    $('backtestNotes').innerHTML =
      '<strong>>=</strong> means the flag was already set ' + backtest.lookback_hours +
      ' hours before the peak, so the true lead is longer than this can measure - not that ' +
      'it is exactly that figure. <strong>MISS</strong> means it never fired in that window. ' +
      '"Away from a top peak" counts flag episodes more than ' + backtest.false_alarm_hours +
      ' hours from any of that gauge\'s top ' + backtest.top_peaks + ' peaks; most are ordinary ' +
      'rain responses rather than errors, but they are why a single flag is not a warning.';
  }


  /* ---------------- routing skill ---------------- */

  /* One source of truth: the palette lives in :root, and the charts and the
     map read it rather than carrying their own copies. Colour used to be
     hardcoded in three JavaScript files, so a theme change meant editing four
     places and trusting them to agree. */
  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  }

  var SERIES = token('--series', '#0d6ea3');
  var ALERT = token('--alert-mark', '#b5651d');

  function renderRouting(routing) {
    var reaches = routing.reaches || [];
    if (!reaches.length) return;

    var travel = reaches.map(function (r) {
      return r.outlet + ' ' + r.travel_time_hours + ' h';
    }).join(', ');

    $('routingSub').textContent =
      'Predicting a downstream gauge from the gauges above it, using water already in the ' +
      'channel. Gradient boosting on upstream level, rate of change shifted by each gauge\'s ' +
      'measured travel time, and catchment rainfall. Scored against persistence - "the level ' +
      'stays where it is" - on a held-out test set split by time, never shuffled. ' +
      'Travel times: ' + travel + '.';

    // Two series only: the flood case for each river. The all-levels numbers
    // are in the table below rather than as four competing lines.
    var colours = [SERIES, ALERT];
    var datasets = reaches.map(function (r, i) {
      return {
        label: r.outlet + ' - gradient boosting',
        data: r.horizons.map(function (h) {
          return { x: h.hours, y: 100 * h.skill_extreme };
        }),
        borderColor: colours[i % 2],
        backgroundColor: colours[i % 2],
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 6,
        tension: 0.2
      };
    });

    // Zero line: below it the model is worse than doing nothing.
    var allHours = reaches[0].horizons.map(function (h) { return h.hours; });
    reaches.forEach(function (r, i) {
      datasets.push({
        label: r.outlet + ' - linear baseline',
        data: r.horizons.map(function (h) {
          return { x: h.hours, y: 100 * h.linear_skill_extreme };
        }),
        borderColor: colours[i % 2],
        backgroundColor: colours[i % 2],
        borderWidth: 1,
        borderDash: [5, 4],
        pointRadius: 0,
        tension: 0.2
      });
    });

    datasets.push({
      label: 'No better than persistence',
      data: [{ x: allHours[0], y: 0 }, { x: allHours[allHours.length - 1], y: 0 }],
      borderColor: token('--axis', '#6d736f'),
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0
    });

    var canvas = $('skillChart');
    canvas.setAttribute('aria-label',
      'Forecast skill against horizon. Skill during floods peaks near 4 hours at about 72% ' +
      'better than persistence, and falls below zero beyond roughly 16 to 24 hours, close to ' +
      'each catchment\u2019s measured travel time. Full figures are in the table below.');

    new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        scales: {
          x: {
            type: 'linear',
            title: { display: true, text: 'hours ahead', color: token('--axis', '#6d736f'),
                     font: { family: 'Inter, sans-serif', size: 11 } },
            ticks: { color: token('--axis', '#6d736f'), font: { family: 'JetBrains Mono, monospace', size: 10 } },
            grid: { color: token('--grid', '#e6e2da'), drawTicks: false },
            border: { color: token('--grid', '#e6e2da') }
          },
          y: {
            title: { display: true, text: 'skill vs persistence', color: token('--axis', '#6d736f'),
                     font: { family: 'Inter, sans-serif', size: 11 } },
            ticks: {
              color: token('--axis', '#6d736f'), font: { family: 'JetBrains Mono, monospace', size: 10 },
              callback: function (v) { return v + '%'; }
            },
            grid: { color: token('--grid', '#e6e2da'), drawTicks: false },
            border: { display: false }
          }
        },
        plugins: {
          legend: {
            display: true,
            labels: { color: token('--muted', '#5c625f'), boxWidth: 10, boxHeight: 10, usePointStyle: true,
                      font: { family: 'Inter, sans-serif', size: 11 } }
          },
          tooltip: {
            backgroundColor: token('--surface', '#ffffff'), borderColor: token('--line', '#ddd8d0'), borderWidth: 1,
            titleColor: token('--text', '#1c1f1d'), bodyColor: token('--text', '#1c1f1d'), padding: 9,
            titleFont: { family: 'JetBrains Mono, monospace', size: 11 },
            bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
            callbacks: {
              title: function (i) { return i[0].parsed.x + ' hours ahead'; },
              label: function (i) {
                return i.dataset.label + ': ' + i.parsed.y.toFixed(1) + '%';
              }
            }
          }
        }
      }
    });

    var worst = [];
    reaches.forEach(function (r) {
      var neg = r.horizons.filter(function (h) { return h.skill_extreme < 0; });
      if (neg.length) {
        worst.push(r.outlet + ' from ' + neg[0].hours + ' h');
      }
    });

    $('routingNote').innerHTML =
      'Skill is <span class="mono">1 - (model error / persistence error)</span>. Zero means no ' +
      'better than assuming the level stays put; <strong class="neg">below zero means worse ' +
      'than doing nothing</strong>' +
      (worst.length ? ' - which happens at ' + worst.join(' and ') + '.' : '.') +
      ' That is why this is capped at the catchment\u2019s travel time rather than extended ' +
      'to days: past it, the water has not entered the river yet.';

    document.querySelector('#routingTable tbody').innerHTML = reaches.map(function (r) {
      return r.horizons.map(function (h) {
        var cls = h.skill_extreme < 0 ? ' class="neg"' : '';
        return '<tr>' +
          '<td>' + r.outlet + '</td>' +
          '<td>' + h.hours + ' h</td>' +
          '<td>' + h.persistence_rmse_m.toFixed(4) + ' m</td>' +
          '<td>' + h.model_rmse_m.toFixed(4) + ' m</td>' +
          '<td>' + (100 * h.skill).toFixed(1) + '%' +
            '<span class="base">linear ' + (100 * h.linear_skill).toFixed(1) + '%</span></td>' +
          '<td' + cls + '>' + (100 * h.skill_extreme).toFixed(1) + '%' +
            '<span class="base">linear ' + (100 * h.linear_skill_extreme).toFixed(1) +
            '% | above ' + h.extreme_threshold_m + ' m</span></td>' +
        '</tr>';
      }).join('');
    }).join('');
  }

  /* ---------------- boot ---------------- */

  var years = 10;

  fetch('data/stations.json')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (index) {
      if (!index) return;
      var rec = index.stations[0].record;
      years = Math.round((new Date(rec.to) - new Date(rec.from)) / (365.25 * 864e5));
      $('attribution').textContent = index.attribution;
      return null;
    })
    .then(function () { return fetch('data/wave.json'); })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (wave) { if (wave) renderWave(wave); return fetch('data/backtest.json'); })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (backtest) {
      if (backtest) renderBacktest(backtest, years);
      return fetch('data/routing.json');
    })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (routing) { if (routing) renderRouting(routing); })
    .catch(function (err) {
      // Log as well as display: a render error inside the chain was previously
      // swallowed here, leaving an empty table and a clean console.
      if (window.console && console.error) console.error('method.js:', err);
      $('waveSub').textContent = 'Could not load the data files - ' + err.message;
    });
  // The space-time view lives here rather than on the monitor page: over a
  // fortnight the travel time is 4% of the width, but over this event it is
  // about a fifth, so the diagonal is the evidence rather than a caveat.
  fetch('data/spacetime_event.json')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !window.DDWSpaceTime) return;
      document.getElementById('stChart').innerHTML = window.DDWSpaceTime.build(d);
      window.DDWSpaceTime.bind(document.getElementById('stChart'));
    })
    .catch(function () { /* the section simply stays empty */ });

})();
