/* Dee & Don Watch - helpers shared by both pages. Loaded first. */

window.DDW = (function () {
  'use strict';

  /* The palette lives in :root; the charts and the map read it from there
     rather than carrying their own copies, so a theme change is one edit. */
  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function fmtDateTime(iso) {
    return new Date(iso).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZone: 'UTC'
    }) + ' UTC';
  }

  function recordYears(record) {
    return Math.round((new Date(record.to) - new Date(record.from)) / (365.25 * 864e5));
  }

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  return { token: token, esc: esc, fmtDateTime: fmtDateTime,
           recordYears: recordYears, MONTHS: MONTHS };
})();
