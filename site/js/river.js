/* The catchment, drawn from real geometry.

   River centrelines and coastline come from OpenStreetMap, fetched once by
   pipeline/bake_geo.py, simplified to ~50 m and committed as a ~42 KB file.
   Gauges sit at their true SEPA coordinates. Equirectangular projection with
   one scale on both axes, so the catchment keeps its real shape.

   No tile layer. Tiles would mean a third-party request on every page view on
   a visitor's behalf, which this project does not make - so the geometry is
   served from our own origin instead. OSM data is ODbL and the footer carries
   the required attribution.

   Two things carry data rather than decoration:
     * the channel SWELLS along its length, interpolated between gauges from
       how high each sits in its own record
     * a gauge running high is larger and ochre - a river in spate carries peat
       and silt and goes brown

   The gauge list below is the accessible twin. */

(function (global) {
  'use strict';

  var W = 1000, H = 560;

  /* The viewBox follows the container's shape instead of being fixed. A fixed
     ratio letterboxes whenever the window is not the shape it assumed, and
     with imagery behind the map those bands show as bare page. */
  function fitHeight() {
    var host = document.getElementById('riverMap');
    if (!host || !host.clientWidth || !host.clientHeight) return 560;
    return Math.max(360, Math.min(960,
      Math.round(W * host.clientHeight / host.clientWidth)));
  }
  // Nothing is overlaid on the map now - the legend moved into the panel - so
  // the padding is only what the scale bar needs.
  var PAD = { l: 44, r: 44, t: 38, b: 58 };

  // Map fills are judged on contrast against the land, not the categorical
  // band - see the note in style.css.
  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  }

  // Read from :root rather than kept here, so the map and the page can never
  // disagree about what colour a river is.
  var C = {
    water: token('--map-water', '#2f7fb0'),
    spate: token('--map-spate', '#b5651d'),
    ground: token('--bg', '#f2efe9'),
    line: token('--line', '#ddd8d0')
  };

  var KM_LAT = 111.2;
  function kmLon(lat) { return 111.32 * Math.cos(lat * Math.PI / 180); }

  var ABERDEEN = { lat: 57.1497, lon: -2.0943 };

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function flagged(s) {
    var l = s.latest;
    return !!(l.is_fast_rise || l.is_high_level || l.is_suspect || l.is_gap ||
              l.is_flatline || l.is_spike);
  }
  function pctOf(s) { return s.latest.level_pct === null ? 0 : s.latest.level_pct; }
  function halfWidth(pct) { return (2.0 + Math.pow(Math.max(pct, 0) / 100, 3) * 11) / 2; }

  function kmToAberdeen(lon, lat) {
    return Math.hypot((lon - ABERDEEN.lon) * kmLon(lat),
                      (lat - ABERDEEN.lat) * KM_LAT);
  }

  /* Framed on the gauges, not the geometry: the OSM extract runs well past the
     catchment, and letting it set the bounds would shrink the gauges to
     nothing. Anything outside the frame is clipped. */
  function projector(stations) {
    var lats = stations.map(function (s) { return s.lat; }).concat(ABERDEEN.lat);
    var lons = stations.map(function (s) { return s.lon; }).concat(ABERDEEN.lon);
    var midLat = (Math.min.apply(null, lats) + Math.max.apply(null, lats)) / 2;
    var padLat = 0.05, padLon = 0.06;

    var x0 = (Math.min.apply(null, lons) - padLon) * kmLon(midLat);
    var x1 = (Math.max.apply(null, lons) + padLon) * kmLon(midLat);
    var y0 = (Math.min.apply(null, lats) - padLat) * KM_LAT;
    var y1 = (Math.max.apply(null, lats) + padLat) * KM_LAT;

    var availW = W - PAD.l - PAD.r, availH = H - PAD.t - PAD.b;
    var scale = Math.min(availW / (x1 - x0), availH / (y1 - y0));
    var offX = PAD.l + (availW - (x1 - x0) * scale) / 2;
    var offY = PAD.t + (availH - (y1 - y0) * scale) / 2;

    return {
      scale: scale,
      x: function (lon) { return offX + (lon * kmLon(midLat) - x0) * scale; },
      y: function (lat) { return offY + (y1 - lat * KM_LAT) * scale; }
    };
  }

  /* Channel width anywhere along the line: interpolated between the two gauges
     that bracket that point by distance downstream. */
  function widthAt(km, gauges) {
    if (!gauges.length) return 1.2;
    if (km >= gauges[0].km) return halfWidth(gauges[0].pct);
    var lastG = gauges[gauges.length - 1];
    if (km <= lastG.km) return halfWidth(lastG.pct);
    for (var i = 0; i < gauges.length - 1; i++) {
      var a = gauges[i], b = gauges[i + 1];
      if (km <= a.km && km >= b.km) {
        var t = (a.km - km) / ((a.km - b.km) || 1);
        return halfWidth(a.pct + (b.pct - a.pct) * t);
      }
    }
    return 1.2;
  }

  function ribbon(pts) {
    if (pts.length < 2) return '';
    var left = [], right = [];
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i];
      var a = pts[Math.max(i - 1, 0)], b = pts[Math.min(i + 1, pts.length - 1)];
      var dx = b.x - a.x, dy = b.y - a.y;
      var len = Math.hypot(dx, dy) || 1;
      var nx = -dy / len, ny = dx / len;
      left.push((p.x + nx * p.hw).toFixed(1) + ',' + (p.y + ny * p.hw).toFixed(1));
      right.unshift((p.x - nx * p.hw).toFixed(1) + ',' + (p.y - ny * p.hw).toFixed(1));
    }
    return '<path d="M' + left.join('L') + 'L' + right.join('L') + 'Z" fill="' +
      C.water + '" opacity="0.95"/>';
  }

  function gaugeMark(s, gx, gy, above, bump) {
    var hot = flagged(s);
    var r = hot ? 6.5 : 4;
    var dy = above ? -(15 + bump) : (19 + bump);
    // A gap or a stalled feed leaves the latest value null; a flagged gauge
    // is exactly where that happens, and toFixed on null took the map down.
    var value = s.latest.value === null ? null : s.latest.value.toFixed(2);
    var out = '<g class="gauge-mark" data-slug="' + s.slug + '" tabindex="0" role="button" ' +
      'aria-label="' + esc(s.name + ', ' + s.river + ', ' +
        (value === null ? 'no current reading' : value + ' metres') + ', higher than ' + pctOf(s).toFixed(0) + ' percent of its record' +
        (hot ? ', running high' : '')) + '">';
    out += '<circle cx="' + gx + '" cy="' + gy + '" r="18" fill="transparent"/>';
    // Drawn under the dot: invisible until hovered, focused or selected.
    out += '<circle class="halo" cx="' + gx + '" cy="' + gy + '" r="' + (r + 5) + '"/>';
    if (hot) {
      out += '<circle cx="' + gx + '" cy="' + gy + '" r="12" fill="' + C.spate +
             '" opacity="0.22"/>';
    }
    out += '<circle class="dot" cx="' + gx + '" cy="' + gy + '" r="' + r + '" fill="' +
           (hot ? C.spate : C.water) + '" stroke="' + C.ground + '" stroke-width="1.8"/>';
    out += '<text x="' + gx + '" y="' + (gy + dy) + '" text-anchor="middle" class="rv-name' +
           (hot ? ' hot' : '') + '">' + esc(s.name) + '</text>';
    if (hot && value !== null) {
      out += '<text x="' + gx + '" y="' + (gy + dy + (above ? -12 : 12)) +
             '" text-anchor="middle" class="rv-val">' + value + ' m</text>';
    }
    return out + '</g>';
  }

  function build(index, geo) {
    H = fitHeight();
    var stations = index.stations;
    var p = projector(stations);
    // role="group", not "img": an image's children are hidden from assistive
    // tech, which left every focusable gauge marker without a name.
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="river-svg" role="group" ' +
      'aria-label="' + esc(summary(index, geo)) + '">';

    svg += '<defs><clipPath id="rvFrame"><rect x="0" y="0" width="' + W + '" height="' +
           (H - PAD.b + 14) + '"/></clipPath></defs><g clip-path="url(#rvFrame)">';

    /* The satellite base, placed by the projector rather than by eye: the image
       is equirectangular over a known bbox, and the projection is linear in
       both lon and lat, so its corners land exactly where the gauges do. */
    var SAT = { s: 56.84, n: 57.47, w: -3.78, e: -1.92 };
    svg += '<image href="geo/satellite.jpg" x="' + p.x(SAT.w).toFixed(1) +
      '" y="' + p.y(SAT.n).toFixed(1) +
      '" width="' + (p.x(SAT.e) - p.x(SAT.w)).toFixed(1) +
      '" height="' + (p.y(SAT.s) - p.y(SAT.n)).toFixed(1) +
      '" preserveAspectRatio="none" class="rv-sat"/>';

    function polyline(line) {
      return line.map(function (c, i) {
        return (i ? 'L' : 'M') + p.x(c[0]).toFixed(1) + ',' + p.y(c[1]).toFixed(1);
      }).join('');
    }

    if (geo && geo.coast) {
      geo.coast.forEach(function (line) {
        svg += '<path class="rv-coast" fill="none" d="' + polyline(line) + '"/>';
      });
    }

    // The rest of the drainage, under everything, as hairlines. The two trunks
    // carry the readings; this is the catchment they sit in, and without it the
    // picture is two lines rather than a river system.
    if (geo && geo.network) {
      geo.network.forEach(function (line) {
        svg += '<path class="rv-net" d="' + polyline(line) + '"/>';
      });
    }

    ['Dee', 'Don'].forEach(function (river) {
      var stem = stations.filter(function (s) { return s.river === river; });
      var gauges = stem
        .map(function (s) { return { km: s.km_to_aberdeen, pct: pctOf(s) }; })
        .sort(function (a, b) { return b.km - a.km; });

      ((geo && geo.rivers && geo.rivers[river]) || []).forEach(function (line) {
        svg += ribbon(line.map(function (c) {
          return { x: p.x(c[0]), y: p.y(c[1]),
                   hw: widthAt(kmToAberdeen(c[0], c[1]), gauges) };
        }));
      });

      stations.filter(function (s) {
        return s.catchment === river && s.river !== river;
      }).forEach(function (t) {
        var own = (geo && geo.tributaries && geo.tributaries[t.river]) || [];
        // One gauge on a tributary means one percentile for the whole of it,
        // so the width is constant rather than interpolated along the line.
        var hw = Math.max(1.2, halfWidth(pctOf(t)) * 0.5);
        if (own.length) {
          own.forEach(function (line) {
            svg += ribbon(line.map(function (c) {
              return { x: p.x(c[0]), y: p.y(c[1]), hw: hw };
            }));
          });
          return;
        }
        // Fallback for a tributary the extract does not carry: a stub to the
        // nearest main-stem gauge, which at least shows where it joins.
        var join = stem.reduce(function (best, s) {
          return Math.abs(s.km_to_aberdeen - t.km_to_aberdeen) <
                 Math.abs(best.km_to_aberdeen - t.km_to_aberdeen) ? s : best;
        }, stem[0]);
        svg += ribbon([
          { x: p.x(t.lon), y: p.y(t.lat), hw: hw },
          { x: p.x(join.lon), y: p.y(join.lat), hw: 1 }
        ]);
      });
    });

    svg += '</g>';

    var barKm = 20, barPx = barKm * p.scale;
    svg += '<g class="rv-axis"><line x1="' + (W - PAD.r - barPx).toFixed(1) + '" y1="' +
      (H - 24) + '" x2="' + (W - PAD.r).toFixed(1) + '" y2="' + (H - 24) + '" stroke="' +
      C.line + '" stroke-width="2"/><text x="' + (W - PAD.r - barPx / 2).toFixed(1) +
      '" y="' + (H - 10) + '" text-anchor="middle" class="rv-tick">' + barKm + ' km</text></g>';


    var ax = p.x(ABERDEEN.lon), ay = p.y(ABERDEEN.lat);
    svg += '<g><circle cx="' + ax.toFixed(1) + '" cy="' + ay.toFixed(1) +
      '" r="3.5" fill="none" stroke="' + C.line + '" stroke-width="1.5"/><text x="' +
      (ax - 12).toFixed(1) + '" y="' + (ay - 10).toFixed(1) +
      '" text-anchor="end" class="rv-city">ABERDEEN</text></g>';

    // Labels last. Seeded with every marker so a name never lands on a dot, and
    // tested in two dimensions - comparing longitude alone put gauges 20 km
    // apart north-to-south in conflict with one another.
    var boxes = stations.map(function (s) {
      return { x: p.x(s.lon), y: p.y(s.lat), hw: 10, hh: 10 };
    });
    // The city label is drawn above, so it has to be in the test too, or a
    // gauge name lands on it - Parkhill did.
    boxes.push({ x: ax - 59, y: ay - 14, hw: 52, hh: 10 });
    function clash(cx, cy, hw, hh) {
      for (var i = 0; i < boxes.length; i++) {
        var b = boxes[i];
        if (Math.abs(cx - b.x) < hw + b.hw && Math.abs(cy - b.y) < hh + b.hh) return true;
      }
      return false;
    }

    stations.slice().sort(function (a, b) {
      return (flagged(b) ? 1 : 0) - (flagged(a) ? 1 : 0);
    }).forEach(function (s) {
      var gx = p.x(s.lon), gy = p.y(s.lat);
      var hw = Math.max(32, s.name.length * 4.3), hh = flagged(s) ? 14 : 8;
      var slot = null;
      for (var k = 0; k < 8 && !slot; k++) {
        var above = k % 2 === 1, bump = Math.floor(k / 2) * 16;
        var cy = gy + (above ? -(15 + bump + hh) : (19 + bump + hh));
        if (!clash(gx, cy, hw, hh)) slot = { above: above, bump: bump, cy: cy };
      }
      if (!slot) slot = { above: false, bump: 48, cy: gy + 67 + hh };
      boxes.push({ x: gx, y: slot.cy, hw: hw, hh: hh });
      svg += gaugeMark(s, gx, gy, slot.above, slot.bump);
    });

    return svg + '</svg>';
  }

  function summary(index, geo) {
    var hot = index.stations.filter(flagged);
    return 'Map of the Dee and Don running east into Aberdeen and the North Sea, with ' +
      index.stations.length + ' gauges at their true positions. ' +
      (hot.length
        ? hot.length + ' running high: ' + hot.map(function (s) { return s.name; }).join(', ') + '.'
        : 'None flagged.') +
      ' The channel is drawn wider where the level sits higher in that gauge\'s own record. ' +
      (geo ? 'River and coast outlines from OpenStreetMap. ' : '') +
      'The same figures are listed below.';
  }

  global.DDWRiver = { build: build };
})(window);
