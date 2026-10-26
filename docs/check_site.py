"""Check what the page actually rendered, not what it logged.

    python check_site.py

A caught-and-logged exception leaves the page broken while `--dump-dom` still
exits clean, because Chrome does not forward page console.error to stderr.
Grepping the log therefore passed for a chart that was plainly blank. These
assertions look at the DOM instead: if drawChart throws, the gauge name is
never written and the rainfall caption keeps its placeholder, so both are
reliable witnesses that the chart was built.

Development check, deliberately outside the repo — the shipped tests cover the
pipeline, and this needs a browser and a running server.
"""

from __future__ import annotations

import re
import subprocess
import sys

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://localhost:8000/"

# (path, [(description, regex that must match the DOM)])
CASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("", [
        ("map drew gauge marks", r'class="gauge-mark"'),
        ("answer line filled", r'id="answerLine"[^>]*>(?!Loading)'),
        ("overlay is hidden on load", r'<div class="modal" id="chartModal"[^>]*\shidden'),
        ("gauge list rendered", r'class="gauge[ "]'),
    ]),
    ("#garthdee:24h", [
        ("gauge name written", r'id="detailName">(?!\u2014)[A-Za-z]'),
        ("rain caption filled", r"bars peak at"),
        ("position readout", r'id="gaugePos"[^>]*>\d+ of 13'),
        # Garthdee is 1.25 h behind (Woodend is quiet) which is inside the
        # three-hour guard, so the projection should draw.
        ("projection drawn", r'id="legendProjected"(?![^>]*\shidden)'),
        ("projection explained", r"routes water"),
    ]),
    ("#parkhill:24h", [
        ("gauge name written", r'id="detailName">(?!—)[A-Za-z]'),
        ("projection drawn", r'id="legendProjected"(?![^>]*\shidden)'),
        ("projection explained", r"routes water"),
    ]),
    ("#woodend:24h", [
        ("gauge name written", r'id="detailName">(?!\u2014)[A-Za-z]'),
        ("no projection on a gauge without one",
         r'id="legendProjected"[^>]*\shidden'),
    ]),
    ("#garthdee:1y:big", [
        ("gauge name written", r'id="detailName">(?!\u2014)[A-Za-z]'),
        ("pop-out open", r'<div class="modal" id="chartModal"(?![^>]*\shidden)'),
        ("gauge picker populated", r'id="gaugeBar"[^>]*>\s*<button'),
        ("long range caption", r"not every reading"),
    ]),
    ("#pitcaple:5y", [
        ("tributary on the long path", r'id="detailName">(?!\u2014)[A-Za-z]'),
    ]),
    ("method.html", [
        ("space-time drew rows", r'class="st-name"'),
        ("skill curve canvas", r'id="skillChart"'),
    ]),
]


def dom(path: str) -> str:
    out = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=16000", "--window-size=1440,1000",
         "--dump-dom", BASE + path],
        capture_output=True, text=True, timeout=120,
        # The DOM carries UTF-8; Windows would otherwise decode it as cp1252.
        encoding="utf-8", errors="replace",
    )
    return out.stdout


def main() -> int:
    failures = 0
    for path, checks in CASES:
        html = dom(path)
        label = "/" + path
        if not html:
            print(f"{label:<22} NO DOM RETURNED")
            failures += 1
            continue
        bad = [name for name, pattern in checks
               if not re.search(pattern, html)]
        if bad:
            failures += len(bad)
            print(f"{label:<22} FAIL")
            for name in bad:
                print(f"{'':<22}   - {name}")
        else:
            print(f"{label:<22} ok  ({len(checks)} checks)")

    print()
    print("all rendered as expected" if not failures
          else f"{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
