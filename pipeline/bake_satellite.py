"""Bake one satellite image of the catchment.

    python pipeline/bake_satellite.py

Run once, not on a schedule. The output is `site/geo/satellite.jpg`, committed
and served from this origin.

Why bake rather than use a tile layer
-------------------------------------
A satellite basemap normally means a tile server, and that means a third-party
request on every page view on a visitor's behalf - which this project does not
make. But the map never pans or zooms: it always shows the same ground. So the
whole thing is exactly one image, fetched once here and served from our own
origin afterwards. No runtime dependency, nothing for anyone else to log.

Source and licence
------------------
Sentinel-2 cloudless by EOX, the **2016** mosaic, which is CC BY 4.0 and so may
be redistributed with attribution. EOX's 2018 and later mosaics are CC BY-NC-SA
and are deliberately NOT used: the share-alike and non-commercial terms would
bind the rest of the site. The required credit is in ATTRIBUTION below and is
shown on the page and in the README.

Projection
----------
Tiles are Web Mercator; this map is equirectangular, with one scale on both
axes. Pasting Mercator tiles straight in would stretch everything north-south
and the rivers would drift off their valleys. The stitched image is therefore
resampled row by row into equirectangular before it is written.
"""

from __future__ import annotations

import io
import math
import sys
import time

import numpy as np
import requests
from PIL import Image

from common import REPO_ROOT

# 2016 mosaic only - see the module docstring on licensing.
LAYER = "s2cloudless_3857"
URL = "https://tiles.maps.eox.at/wmts/1.0.0/{layer}/default/g/{z}/{y}/{x}.jpg"
ATTRIBUTION = ("Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH "
               "(Contains modified Copernicus Sentinel data 2016 & 2017)")
UA = "dee-and-don-watch/1.0 (portfolio project; one-off basemap bake)"

# Wider than bake_geo.py's frame on purpose: the drawn viewBox extends past
# the padded gauge bbox, and anything the image does not cover shows as bare
# page behind the map.
LAT_S, LAT_N = 56.84, 57.47
LON_W, LON_E = -3.78, -1.92
ZOOM = 11                     # ~45 m per pixel at this latitude
OUT_WIDTH = 2200


def merc_y(lat: float) -> float:
    """Normalised Web Mercator y, 0 at the north pole edge, 1 at the south."""
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def tile_range(z: int) -> tuple[int, int, int, int]:
    n = 2 ** z
    x0 = int((LON_W + 180.0) / 360.0 * n)
    x1 = int((LON_E + 180.0) / 360.0 * n)
    y0 = int(merc_y(LAT_N) * n)
    y1 = int(merc_y(LAT_S) * n)
    return x0, x1, y0, y1


def fetch_tile(z: int, x: int, y: int, attempts: int = 4) -> Image.Image | None:
    url = URL.format(layer=LAYER, z=z, y=y, x=x)
    for attempt in range(attempts):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        except (requests.RequestException, OSError) as exc:
            wait = 4 * (attempt + 1)
            print(f"    {z}/{x}/{y} failed ({type(exc).__name__}); retry in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    return None


def main() -> int:
    x0, x1, y0, y1 = tile_range(ZOOM)
    across, down = x1 - x0 + 1, y1 - y0 + 1
    total = across * down
    print(f"Sentinel-2 cloudless 2016 (CC BY 4.0), zoom {ZOOM}")
    print(f"{across} x {down} = {total} tiles\n")

    stitched = Image.new("RGB", (across * 256, down * 256))
    got = 0
    for ix, x in enumerate(range(x0, x1 + 1)):
        for iy, y in enumerate(range(y0, y1 + 1)):
            tile = fetch_tile(ZOOM, x, y)
            if tile is None:
                print(f"  missing tile {ZOOM}/{x}/{y}", file=sys.stderr)
                continue
            stitched.paste(tile, (ix * 256, iy * 256))
            got += 1
            time.sleep(0.12)        # be polite to a free service
        print(f"  column {ix + 1}/{across}  ({got}/{total} tiles)")

    if got < total * 0.8:
        print(f"Only {got}/{total} tiles arrived; not writing a holed image",
              file=sys.stderr)
        return 1

    # Crop the stitched sheet to the exact bbox, still in Mercator.
    n = 2 ** ZOOM
    px_w, px_h = n * 256, n * 256
    left = (LON_W + 180.0) / 360.0 * px_w - x0 * 256
    right = (LON_E + 180.0) / 360.0 * px_w - x0 * 256
    top = merc_y(LAT_N) * px_h - y0 * 256
    bottom = merc_y(LAT_S) * px_h - y0 * 256
    merc = stitched.crop((int(left), int(top), int(right), int(bottom)))

    # Mercator -> equirectangular. x is linear in longitude in both, so only
    # the rows move: each output row is the source row for that latitude.
    out_h = int(round(OUT_WIDTH * (LAT_N - LAT_S) /
                      ((LON_E - LON_W) * math.cos(math.radians((LAT_N + LAT_S) / 2)))))
    merc = merc.resize((OUT_WIDTH, merc.height), Image.LANCZOS)
    src = np.asarray(merc)

    y_north, y_south = merc_y(LAT_N), merc_y(LAT_S)
    lats = np.linspace(LAT_N, LAT_S, out_h)
    rows = (np.array([merc_y(v) for v in lats]) - y_north) / (y_south - y_north)
    idx = np.clip((rows * (src.shape[0] - 1)).round().astype(int), 0, src.shape[0] - 1)

    out = Image.fromarray(src[idx])
    path = REPO_ROOT / "site" / "geo" / "satellite.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, "JPEG", quality=82, optimize=True, progressive=True)

    print(f"\nWrote {path.relative_to(REPO_ROOT)} - {out.width}x{out.height}, "
          f"{path.stat().st_size / 1024:.0f} KB")
    print(f"Attribution required on the page and in the README:\n  {ATTRIBUTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
