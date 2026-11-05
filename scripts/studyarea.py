#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""studyarea.py — journal-grade study-area figure, one entry point.

No absolute paths are baked in: everything comes from the command line.

  python studyarea.py prepare --workdir W --adcode 522729 --prefecture 522700 --province 520000
  python studyarea.py render  --workdir W --adcode 522729 --name "长顺县" --name-en "Changshun county" \
                              --out OUT --lang en --themes themes.json --points points.csv

Layout produced by `render` (panels that need unavailable data are dropped, not
faked):

  (a) China, province highlighted, South China Sea islets + ten-dash line in the
      map body (no inset box)
  (b) region, all county-level borders of the province, study county in cyan
      with a red outline, red connectors from (a)
  (c) hypsometry over shaded relief, with hydrography and river names
  (d..) one panel per theme raster given by --themes
  (f-1) county border with neighbours, (f-2) DEM valley skeleton,
  (f-3) hypsometric curve + slope-area bars

Authoritative style values live in style_spec.json (current_defaults).
"""
import argparse, json, math, os, shutil, subprocess, sys, tempfile
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch
from matplotlib.path import Path as MplPath
import matplotlib.patheffects as pe

# Windows consoles default to a legacy codepage; unit symbols such as km² and
# any CJK text in progress output would abort the run without this.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATAV = "https://geo.datav.aliyun.com/areas_v3/bound/{code}{full}.json"
TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
GREENS = ["#1a9850", "#66bd63", "#a6d96a", "#d9ef8b",
          "#fee08b", "#fdae61", "#f46d43", "#b2182b"]
CYAN, RED, WATER = "#29ABE2", "#E51B22", "#1F6FC4"
CJK_FALLBACK = ["Times New Roman", "Microsoft YaHei", "SimHei"]
SLOPE_BINS = [0, 2, 6, 15, 25, 90]
SLOPE_LAB = ["<2", "2–6", "6–15", "15–25", ">25"]
SLOPE_COL = ["#cfe3c8", "#f0d264", "#e8863a", "#c2352b", "#6e1414"]


# ---------------------------------------------------------------- utilities
def die(msg):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(1)


def dl(url, dest):
    """curl first (urllib is unreliable for S3 from this region), urllib as backup."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if shutil.which("curl"):
        r = subprocess.run(["curl", "-sSL", "--max-time", "120", "-o", dest, url])
        if r.returncode == 0 and os.path.getsize(dest) > 0:
            return
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=120) as f, open(dest, "wb") as g:
            g.write(f.read())
    except Exception as e:
        die("download failed for %s (%s)" % (url, e))


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pick(workdir, *cands):
    """Return the first existing path among candidates (relative to workdir)."""
    for c in cands:
        p = c if os.path.isabs(c) else os.path.join(workdir, c)
        if os.path.exists(p):
            return p
    return None


def rings_of(g):
    if g["type"] == "Polygon":
        return [g["coordinates"][0]]
    if g["type"] == "MultiPolygon":
        return [p[0] for p in g["coordinates"]]
    return []


def centroid_of(g):
    xs, ys = [], []
    for r in rings_of(g):
        for p in r:
            xs.append(p[0]); ys.append(p[1])
    return sum(xs) / len(xs), sum(ys) / len(ys)


def county_mask(rings, X, Y):
    m = np.zeros(X.shape, bool)
    pts = np.column_stack([X.ravel(), Y.ravel()])
    for r in rings:
        m |= MplPath(np.asarray(r)).contains_points(pts).reshape(X.shape)
    return m


def check_font():
    """matplotlib silently substitutes if Times New Roman is absent — warn loudly."""
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    if "Times New Roman" in names:
        return "Times New Roman"
    for alt in ("Liberation Serif", "Tinos", "DejaVu Serif"):
        if alt in names:
            sys.stderr.write("WARNING: Times New Roman not installed; using %s. "
                             "Figure metrics will differ from the reference.\n" % alt)
            return alt
    sys.stderr.write("WARNING: no serif font found; matplotlib will substitute.\n")
    return "serif"


# ---------------------------------------------------------------- prepare
def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    xt = (lon + 180.0) / 360.0 * n
    yt = (1.0 - math.log(math.tan(math.radians(lat)) +
                         1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return xt, yt


def cmd_prepare(a):
    wd, z = a.workdir, a.zoom
    d = os.path.join(wd, "data")
    os.makedirs(d, exist_ok=True)
    for code, full in [(a.adcode, ""), (a.adcode, "_full"), (a.prefecture, ""),
                       (a.province, ""), (a.province, "_full"),
                       (a.prefecture, "_full"), ("100000", ""), ("100000", "_full")]:
        dl(DATAV.format(code=code, full=full), os.path.join(d, "%s%s.json" % (code, full)))

    # every county of the province, via its prefecture-level _full files
    prov = load_json(os.path.join(d, "%s_full.json" % a.province))
    feats = []
    for f in prov["features"]:
        code = str(f["properties"]["adcode"])
        p = os.path.join(d, "county_%s.json" % code)
        dl(DATAV.format(code=code, full="_full"), p)
        try:
            for g in load_json(p)["features"]:
                feats.append({"type": "Feature", "geometry": g["geometry"],
                              "properties": {"name": g["properties"]["name"],
                                             "adcode": str(g["properties"]["adcode"]),
                                             "center": g["properties"].get("center")}})
        except Exception as e:
            sys.stderr.write("  skip %s (%s)\n" % (code, e))
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(d, "counties.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print("county-level features collected:", len(feats))

    # DEM tiles over the county plus a margin
    cs = load_json(os.path.join(d, "%s.json" % a.adcode))["features"][0]["geometry"]
    rr = [p for r in rings_of(cs) for p in r]
    lon0, lon1 = min(p[0] for p in rr) - a.lon_margin, max(p[0] for p in rr) + a.lon_margin
    lat0, lat1 = min(p[1] for p in rr) - a.lat_margin, max(p[1] for p in rr) + a.lat_margin
    x0, y0 = lonlat_to_tile(lon0, lat1, z)
    x1, y1 = lonlat_to_tile(lon1, lat0, z)
    tiles = os.path.join(wd, "dem_tiles")
    os.makedirs(tiles, exist_ok=True)
    from PIL import Image
    px = []
    for tx in range(int(x0), int(x1) + 1):
        for ty in range(int(y0), int(y1) + 1):
            f = os.path.join(tiles, "%d_%d_%d.png" % (z, tx, ty))
            dl(TILE.format(z=z, x=tx, y=ty), f)
            px.append((tx, ty, Image.open(f).convert("RGB")))
    nx = int(x1) - int(x0) + 1
    ny = int(y1) - int(y0) + 1
    big = np.zeros((ny * 256, nx * 256, 3), np.uint8)
    for tx, ty, im in px:
        big[(ty - int(y0)) * 256:(ty - int(y0) + 1) * 256,
            (tx - int(x0)) * 256:(tx - int(x0) + 1) * 256] = np.asarray(im)
    elev = big[:, :, 0] * 256 + big[:, :, 1] + big[:, :, 2] / 256.0 - 32768

    rows, cols = elev.shape
    # uniform lat/lon grid across the tile block
    west = int(x0) / 2 ** z * 360.0 - 180.0
    east = (int(x0) + nx) / 2 ** z * 360.0 - 180.0
    north = 180.0 - 2 * 180.0 / math.pi * math.atan(
        math.sinh(math.pi * (1 - 2 * int(y0) / 2 ** z)))
    south = 180.0 - 2 * 180.0 / math.pi * math.atan(
        math.sinh(math.pi * (1 - 2 * (int(y0) + ny) / 2 ** z)))
    glon = np.linspace(west, east, cols)
    glat = np.linspace(north, south, rows)
    np.savez_compressed(os.path.join(wd, "dem.npz"), elev=elev.astype(np.float32),
                        lons=glon, lats=glat)
    print("dem.npz %s  lon %.3f..%.3f lat %.3f..%.3f" % (elev.shape, west, east, south, north))

    # valley skeleton (fallback hydrography, drawn in panel f-2)
    from scipy import ndimage
    sm = ndimage.gaussian_filter(elev.astype(np.float64), 4)
    relief = sm - elev
    try:
        from skimage.morphology import skeletonize
    except ImportError:
        skeletonize = None
    if skeletonize is None:
        sys.stderr.write("WARNING: scikit-image missing; valleys.npz written empty. "
                         "pip install scikit-image to enable panel (f-2).\n")
        lon_all = lat_all = lon_deep = lat_deep = np.zeros(0)
    else:
        def net(thr):
            sk = skeletonize(relief > thr)
            for _ in range(3):
                nb = ndimage.convolve(sk.astype(np.uint8), np.ones((3, 3), np.uint8),
                                      mode="constant") - sk
                sk &= (nb >= 2)
            ii, jj = np.where(sk)
            return lons[jj], lats[ii]
        lon_all, lat_all = net(16.0)
        lon_deep, lat_deep = net(40.0)
    np.savez_compressed(os.path.join(wd, "valleys.npz"), lon_all=lon_all,
                        lat_all=lat_all, lon_deep=lon_deep, lat_deep=lat_deep)
    print("valleys.npz: %d skeleton px, %d deep-valley px" % (len(lon_all), len(lon_deep)))

    # slope
    dl_ = (glon[1] - glon[0]) * 111320.0 * math.cos(math.radians(glat.mean()))
    dt_ = abs(glat[1] - glat[0]) * 110540.0
    gy, gx = np.gradient(elev, dt_, dl_)
    np.savez_compressed(os.path.join(wd, "slope.npz"),
                        slope=np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32))
    print("prepare done")


# ---------------------------------------------------------------- themes
def read_theme(spec, workdir, glon, glat, mask):
    """Read a theme raster (GeoTIFF, or a TIFF inside a zip) and align it to the
    DEM grid by nearest reprojection. Returns (class_index, classes, areas)."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds
    src = spec["source"]
    if not os.path.isabs(src):
        src = os.path.join(workdir, src)
    path = src
    if src.lower().endswith(".zip"):
        import zipfile
        entry = spec.get("entry")
        if not entry:
            zf = zipfile.ZipFile(src)
            entry = [i.filename for i in zf.infolist()
                     if i.filename.lower().endswith((".tif", ".tiff"))][0]
            sys.stderr.write("  zip entry: %r\n" % entry)
        path = "/vsizip/%s/%s" % (src.replace("\\", "/"), entry)
    H, W = mask.shape
    west, east = float(glon[0]), float(glon[-1])
    north, south = float(glat[0]), float(glat[-1])
    with rasterio.open(path) as ds:
        out = np.full((H, W), int(spec.get("nodata", 255)), ds.dtypes[0])
        reproject(source=ds.read(1), destination=out, src_transform=ds.transform,
                  src_crs=ds.crs,
                  dst_transform=from_bounds(west, south, east, north, W, H),
                  dst_crs="EPSG:4326", resampling=Resampling.nearest,
                  src_nodata=ds.nodata)
    vals = [c["value"] for c in spec["classes"]]
    idx = np.full(out.shape, -1, np.int8)
    for i, v in enumerate(vals):
        idx[out == v] = i
    idx[~mask] = -1
    # per-class area, weighted by cos(lat) because the grid is regular in degrees
    cell = (np.cos(np.radians(glat))[:, None]
            * ((glon[1] - glon[0]) * 111320.0 * abs(glat[1] - glat[0]) * 110540.0)
            / 1e6) * np.ones((len(glat), len(glon)))
    tot = int(mask.sum())
    areas = []
    for i, v in enumerate(vals):
        n = int((idx == i).sum())
        areas.append((float(cell[mask][idx[mask] == i].sum()), 100.0 * n / tot))
    nodata_pct = 100.0 * int(((idx < 0) & mask).sum()) / tot
    return idx, areas, nodata_pct


# ---------------------------------------------------------------- render
def cmd_render(a):
    wd = a.workdir
    d = os.path.join(wd, "data")
    dem_p = pick(wd, "dem.npz")
    if not dem_p:
        die("dem.npz not found — run `prepare` first")
    dd = np.load(dem_p)
    elev, lons, lats = dd["elev"].astype(np.float64), dd["lons"], dd["lats"]
    H, W = elev.shape
    dlon, dlat = lons[1] - lons[0], lats[0] - lats[1]
    lat_mid = 0.5 * (lats[0] + lats[-1])
    mx, my = dlon * 111320.0 * math.cos(math.radians(lat_mid)), abs(dlat) * 110540.0

    cs = load_json(os.path.join(d, "%s.json" % a.adcode))["features"][0]["geometry"]
    rings = rings_of(cs)
    pts = [p for r in rings for p in r]
    CB = (min(p[0] for p in pts), max(p[0] for p in pts),
          min(p[1] for p in pts), max(p[1] for p in pts))
    cs_cx, cs_cy = centroid_of(cs)
    X, Y = np.meshgrid(lons, lats)
    mask = county_mask(rings, X, Y)

    china_p = pick(d, "100000_full.json")
    cn_line_p = pick(d, "100000.json")
    prov_p = pick(d, "%s_full.json" % a.province)
    pref_p = pick(d, "%s.json" % a.prefecture)
    counties_p = pick(d, "counties.json", "gz_counties.json")
    rivers_p = pick(d, "rivers.json", "osm_rivers.json")
    water_p = pick(d, "water.json", "osm_water.json")
    valleys_p = pick(wd, "valleys.npz")
    slope_p = pick(wd, "slope.npz", "hydro2.npz")
    nine_p = (pick(d, "ninedash.shp", "ninedash.geojson")
              or pick(d, "ninedash/china_nine_dotted_line.shp",
                      "ninedash/china_nine_dotted_line.geojson"))
    if not (china_p and cn_line_p and prov_p and pref_p):
        die("missing base boundary jsons in %s — run `prepare`" % d)

    china_full = load_json(china_p)["features"]
    # Province and county display names are read from the GeoJSON by adcode, not
    # passed on the command line: CJK argv gets mangled by the Windows console
    # codepage, which is exactly the kind of failure a shared tool must avoid.
    def name_by_code(code, default=None):
        for f in china_full:
            if str(f["properties"].get("adcode", "")) == str(code):
                return f["properties"].get("name") or default
        for f in china_full:
            if str(f["properties"].get("name", "")).startswith(str(code)[:2]):
                pass
        return default
    prov_name = a.province_name or name_by_code(a.province)
    if not prov_name:
        die("cannot resolve province name for adcode %s from the country file; "
            "pass --province-name" % a.province)
    county_feat = load_json(os.path.join(d, "%s.json" % a.adcode))["features"][0]
    disp = a.name or county_feat["properties"].get("name") or str(a.adcode)
    title_c = a.title_c or disp
    themes = load_json(a.themes) if a.themes else []
    if not a.themes:
        auto = pick(wd, "themes.json")
        if auto:
            themes = load_json(auto)
    points = None
    if a.points:
        points = np.genfromtxt(a.points, delimiter=",", names=True, dtype=None,
                               encoding="utf-8")
        if points.dtype.names is None:
            die("points CSV must have a header: lon,lat[,label]")

    fam = check_font()
    plt.rcParams["font.family"] = fam
    plt.rcParams["axes.unicode_minus"] = False
    lang = a.lang

    # ---- hillshade ----
    gp = np.gradient(elev, axis=1) / mx
    gq = -np.gradient(elev, axis=0) / my
    hs = np.clip((-gp * -0.5 - gq * 0.5 + 0.7071) / np.sqrt(gp ** 2 + gq ** 2 + 1), 0, 1)
    hs = np.clip(0.82 + 0.30 * (hs - hs.mean()) / (hs.std() + 1e-9), 0.70, 1.08)

    ins = elev[mask]
    vmin, vmax = int(ins.min()), int(ins.max())
    p2, p98 = np.percentile(ins, [2, 98])
    ramp = LinearSegmentedColormap.from_list("gr", GREENS, N=256)
    rgb = ramp(np.clip((elev - p2) / (p98 - p2), 0, 1))[:, :, :3] * hs[:, :, None]
    dem_img = np.ones(elev.shape + (3,))
    dem_img[mask] = np.clip(rgb[mask], 0, 1)

    # ---- theme panels ----
    theme_layers = []
    for t in themes:
        try:
            idx, areas, nd = read_theme(t, wd, lons, lats, mask)
        except Exception as e:
            sys.stderr.write("WARNING: theme %r skipped: %s\n" % (t.get("title"), e))
            continue
        cols = [c["color"] for c in t["classes"]]
        img = np.ones(elev.shape + (3,))
        lut = np.array([matplotlib.colors.to_rgb(c) for c in cols])
        ok = idx >= 0
        img[ok] = lut[idx[ok]]
        theme_layers.append({"title": t["title"], "classes": t["classes"],
                             "areas": areas, "nodata": nd, "img": img})
        print("theme %-34s nodata %.1f%%  %s" % (
            t["title"], nd,
            ", ".join("%s %.1f km² (%.1f%%)" % (c["label"], areas[i][0], areas[i][1])
                      for i, c in enumerate(t["classes"]))))

    # ---- hydrography ----
    riv = load_json(rivers_p) if rivers_p else None
    wat = load_json(water_p) if water_p else None
    names_map = load_json(a.names) if (a.names and os.path.exists(a.names)) else {}
    unmapped = set()
    vl = np.load(valleys_p) if valleys_p else None
    slp = None
    if slope_p:
        zz = np.load(slope_p)
        slp = zz["slope"] if "slope" in zz else zz["elev_f"] * 0

    def en(nm):
        if nm in names_map:
            return names_map[nm]
        unmapped.add(nm)
        return None

    def clip_pts(lon_arr, lat_arr):
        ib = ((lon_arr >= CB[0]) & (lon_arr <= CB[1]) &
              (lat_arr >= CB[2]) & (lat_arr <= CB[3]))
        ii = np.clip(((lat_arr[ib] - lats[0]) / -dlat).astype(int), 0, H - 1)
        jj = np.clip(((lon_arr[ib] - lons[0]) / dlon).astype(int), 0, W - 1)
        keep = mask[ii, jj]
        idx = np.where(ib)[0][keep]
        return lon_arr[idx], lat_arr[idx]

    if vl is not None:
        sk_lon, sk_lat = clip_pts(vl["lon_all"], vl["lat_all"])
        dp_lon, dp_lat = clip_pts(vl["lon_deep"], vl["lat_deep"])
        print("skeleton clipped to county: %d px (deep %d)" % (len(sk_lon), len(dp_lon)))

    def draw_hydro(ax, with_names=True):
        if wat:
            for f in wat["features"]:
                for r in rings_of(f["geometry"]):
                    xs = [c[0] for c in r]; ys = [c[1] for c in r]
                    ax.fill(xs + xs[:1], ys + ys[:1], color=WATER, alpha=0.85, zorder=5)
        if riv:
            for f in riv["features"]:
                g = f["geometry"]
                p = g["coordinates"] if g["type"] == "LineString" else g["coordinates"][0]
                fc = f["properties"].get("fclass")
                ax.plot([c[0] for c in p], [c[1] for c in p],
                        color=WATER if fc == "river" else "#5b93d6",
                        lw=1.15 if fc == "river" else 0.45, zorder=7,
                        solid_capstyle="round",
                        path_effects=[pe.withStroke(linewidth=1.6, foreground=(1, 1, 1, .45))])
        if not with_names or not riv:
            return
        best = {}
        for f in riv["features"]:
            nm = f["properties"].get("name")
            if not nm:
                continue
            L = f["properties"].get("length_deg", 0)
            if nm not in best or L > best[nm][1]:
                best[nm] = (f, L)
        for nm0, (f, L) in sorted(best.items(), key=lambda x: -x[1][1]):
            nm = en(nm0) if (names_map or lang == "zh") else None
            if not nm:
                continue
            p = f["geometry"]["coordinates"]
            k = int(len(p) * 0.42)
            ang = math.degrees(math.atan2(p[min(k + 3, len(p) - 1)][1] - p[max(k - 3, 0)][1],
                                          p[min(k + 3, len(p) - 1)][0] - p[max(k - 3, 0)][0]))
            if ang > 90:
                ang -= 180
            elif ang < -90:
                ang += 180
            ax.text(p[k][0], p[k][1], nm, fontsize=9.0, style="italic", color="#14477d",
                    family=CJK_FALLBACK if lang == "zh" else fam, ha="center",
                    va="center", rotation=ang, zorder=14,
                    path_effects=[pe.withStroke(linewidth=2.6, foreground="white")])
        if wat:
            for f in wat["features"]:
                nm0 = f["properties"].get("name")
                if not nm0:
                    continue
                nm = en(nm0) if (names_map or lang == "zh") else None
                if not nm:
                    continue
                c = rings_of(f["geometry"])[0]
                ax.text(sum(q[0] for q in c) / len(c), sum(q[1] for q in c) / len(c),
                        nm, fontsize=8.2, style="italic", color="#14477d",
                        family=CJK_FALLBACK if lang == "zh" else fam, ha="center",
                        va="center", zorder=14, path_effects=[pe.withStroke(linewidth=2.4, foreground="white")])

    # ---- geometry helpers ----
    FW, FH = 13.4, 9.6
    TOP, BOT, GAP = 0.952, 0.072, 0.028
    LW = 0.300
    da_a = (136.2 - 73.4) * math.cos(math.radians(30.0)) / (54.4 - 3.2)
    da_b = (113.0 - 100.5) * math.cos(math.radians(28.0)) / 10.0
    avail = (TOP - BOT) * FH - GAP * FH
    need = LW * FW * (1 / da_a + 1 / da_b)
    slack = max(0.0, avail - need) / FH
    A_H = LW * FW / (da_a * FH) + slack / 2
    B_H = LW * FW / (da_b * FH) + slack / 2

    PADX, PADY = 0.05, 0.04
    ext = (CB[0] - PADX, CB[1] + PADX, CB[2] - PADY, CB[3] + PADY)
    da = ((ext[1] - ext[0]) * math.cos(math.radians(lat_mid))) / (ext[3] - ext[2])
    RX = 0.062 + LW + 0.028
    ncol = 1 + len(theme_layers)          # (c) plus one column per theme
    RW = 0.945 - RX
    HALF = RW / ncol
    STRIP_H = 0.112 if theme_layers else 0.075
    FP_MIN = 0.24          # floor height needed by the two stacked charts
    MAP_H = min(HALF * FW / (da * FH), TOP - BOT - 2 * GAP - STRIP_H - FP_MIN)
    MAPS_Y = TOP - MAP_H
    STRIP_Y = MAPS_Y - 0.010 - STRIP_H
    BLK = (RX, STRIP_Y - 0.006, RW, TOP - (STRIP_Y - 0.006))
    FP_H = max(0.10, (BLK[1] - GAP) - BOT)
    print("layout: %d columns, MAP_H %.3f, block %.3f..%.3f" % (ncol, MAP_H, BLK[1], TOP))

    fig = plt.figure(figsize=(FW, FH), dpi=100)
    kmpd = 111320.0 * math.cos(math.radians(lat_mid)) / 1000.0

    def fit(ax, rect, lon0, lon1, lat0, lat1, lref):
        w, ht = rect[2] * FW, rect[3] * FH
        ra, cosr = w / ht, math.cos(math.radians(lref))
        da_ = (lon1 - lon0) * cosr / (lat1 - lat0)
        if ra > da_:
            nd = (lat1 - lat0) * ra / cosr; m = 0.5 * (lon0 + lon1)
            xl, yl = (m - nd / 2, m + nd / 2), (lat0, lat1)
        else:
            nd = (lon1 - lon0) * cosr / ra; m = 0.5 * (lat0 + lat1)
            xl, yl = (lon0, lon1), (m - nd / 2, m + nd / 2)
        ax.set_position(rect); ax.set_xlim(*xl); ax.set_ylim(*yl); ax.set_aspect("auto")
        return xl, yl

    def frame(ax, lw=1.2):
        for sp in ax.spines.values():
            sp.set_linewidth(lw); sp.set_color("black")
        ax.set_facecolor("white")

    def letter(ax, s, title=None, x=0.985, y=0.975, fs=14):
        txt = "(%s)" % s if not title else "(%s)  %s" % (s, title)
        ax.text(x, y, txt, transform=ax.transAxes,
                fontsize=fs, fontweight="bold", family=fam,
                va="top" if y > 0.5 else "bottom", ha="right" if title is None else "left",
                zorder=60, color="black", clip_on=False)

    def dms(v, ax_):
        dd = int(v); mm = int(round((v - dd) * 60))
        if mm == 60:
            dd += 1; mm = 0
        u = "E" if ax_ == "lon" else "N"
        return "%d°%s" % (dd, u) if mm == 0 else "%d°%02d′%s" % (dd, mm, u)

    def ticks(ax, side, size=11.0):
        ax.tick_params(labelsize=size, length=4.5, direction="in", top=True,
                       right=(side == "right"), labeltop=True,
                       labelleft=(side == "left"), labelright=(side == "right"),
                       labelbottom=False, pad=2)
        for sp in ax.spines.values():
            sp.set_linewidth(0.7); sp.set_color("black")
        if side != "both":
            ax.spines["right" if side == "left" else "left"].set_visible(False)
        for t in ax.get_xticklabels():
            t.set_fontweight("bold"); t.set_family(fam)
        for t in ax.get_yticklabels():
            t.set_fontweight("bold"); t.set_family(fam)
            t.set_rotation(90); t.set_va("center")
            t.set_ha({"left": "right", "right": "left"}.get(side, "center"))

    def north(ax, x, y, h):
        hw = h * .26
        ax.add_patch(plt.Polygon([[x, y], [x - hw, y - h], [x, y - h * .72]], color="k",
                                 zorder=42, clip_on=False))
        ax.add_patch(plt.Polygon([[x, y], [x + hw, y - h], [x, y - h * .72]], fc="w",
                                 ec="k", lw=.8, zorder=42, clip_on=False))
        ax.text(x, y + h * .16, "N", ha="center", va="bottom", fontsize=10,
                fontweight="bold", family=fam, zorder=42, clip_on=False)

    def sbar(ax, x0, y0, km, nseg=4, fs=6.5):
        h = .012 * (ax.get_ylim()[1] - ax.get_ylim()[0])
        deg = km / kmpd; seg = deg / nseg
        for i in range(nseg):
            ax.add_patch(Rectangle((x0 + i * seg, y0), seg, h, fc="k" if i % 2 == 0 else "w",
                                   ec="k", lw=.8, zorder=42))
        for i in range(nseg + 1):
            ax.text(x0 + i * seg, y0 + h * 1.4, str(int(round(i * km / nseg))),
                    ha="center", va="bottom", fontsize=fs, zorder=42)
        ax.text(x0 + deg * 1.03, y0 + h * 1.4, "Km", ha="left", va="bottom",
                fontsize=fs, zorder=42)

    # (a) China
    axa = fig.add_axes([0, 0, 1, 1])
    fit(axa, (0.062, TOP - A_H, LW, A_H), 73.4, 136.2, 3.2, 54.4, 30.0)
    y0, y1 = axa.get_ylim()
    pad = (y1 - y0) - (54.4 - 3.2)
    if pad > 0:
        axa.set_ylim(y0 - pad * .6, y1 + pad * .4)
    for f in load_json(china_p)["features"]:
        nm = f["properties"].get("name")
        for r in rings_of(f["geometry"]):
            xs = [c[0] for c in r]; ys = [c[1] for c in r]
            axa.fill(xs, ys, color=CYAN if nm == prov_name else "white", zorder=2)
            axa.plot(xs + xs[:1], ys + ys[:1], color="#8a8a8a", lw=.35, zorder=3)
    for r in rings_of(load_json(cn_line_p)["features"][0]["geometry"]):
        xs = [c[0] for c in r]; ys = [c[1] for c in r]
        axa.plot(xs + xs[:1], ys + ys[:1], color="k", lw=1.1, zorder=6)
    for f in load_json(china_p)["features"]:
        if f["properties"].get("name") == prov_name:
            for r in rings_of(f["geometry"]):
                xs = [c[0] for c in r]; ys = [c[1] for c in r]
                axa.plot(xs + xs[:1], ys + ys[:1], color=RED, lw=1.7, zorder=7)
    if nine_p:
        if nine_p.endswith(".geojson"):
            segs = [f["geometry"]["coordinates"] for f in load_json(nine_p)["features"]]
        else:
            import shapefile
            segs = [s.points for s in shapefile.Reader(nine_p).shapes()]
        for seg in segs:
            axa.plot([c[0] for c in seg], [c[1] for c in seg], color="k", lw=1.0, zorder=5)
    axa.text(103.5, 41.0, "China" if lang == "en" else "中国", fontsize=20,
             fontweight="bold", family=CJK_FALLBACK if lang == "zh" else fam,
             ha="center", zorder=12)
    axa.set_xticks([75, 90, 105, 120, 135])
    axa.set_yticks([t for t in [10, 20, 30, 40, 50] if axa.get_ylim()[0] <= t <= axa.get_ylim()[1]])
    axa.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lon")))
    axa.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lat")))
    frame(axa); ticks(axa, "left", 9.5)
    letter(axa, "a")

    # (b) region
    axb = fig.add_axes([0, 0, 1, 1])
    fit(axb, (0.062, BOT, LW, B_H), 100.5, 113.0, 23.0, 33.0, 28.0)
    for f in load_json(china_p)["features"]:
        for r in rings_of(f["geometry"]):
            xs = [c[0] for c in r]; ys = [c[1] for c in r]
            axb.plot(xs + xs[:1], ys + ys[:1], color="k", lw=.7, zorder=4)
    if counties_p:
        for f in load_json(counties_p)["features"]:
            for r in rings_of(f["geometry"]):
                xs = [c[0] for c in r]; ys = [c[1] for c in r]
                axb.plot(xs + xs[:1], ys + ys[:1], color="#7a7a7a", lw=.32, zorder=5)
    for f in load_json(prov_p)["features"]:
        if str(f["properties"]["adcode"]) == str(a.prefecture):
            for r in rings_of(f["geometry"]):
                xs = [c[0] for c in r]; ys = [c[1] for c in r]
                axb.plot(xs + xs[:1], ys + ys[:1], color="k", lw=1.0, zorder=5)
    for r in rings:
        xs = [c[0] for c in r]; ys = [c[1] for c in r]
        axb.fill(xs, ys, color=CYAN, zorder=6)
        axb.plot(xs + xs[:1], ys + ys[:1], color=RED, lw=1.5, zorder=7)
    axb.set_xticks([102, 106, 110, 114]); axb.set_yticks([24, 28, 32])
    axb.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lon")))
    axb.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lat")))
    frame(axb); ticks(axb, "left", 9.5)
    letter(axb, "b")
    inv = fig.transFigure.inverted()
    gzc = [f for f in load_json(china_p)["features"]
           if f["properties"].get("name") == prov_name]
    if gzc:
        gx, gy = centroid_of(gzc[0]["geometry"])
        for off in ((-0.95, 0.30), (-0.95, -0.30)):
            pa = inv.transform(axa.transData.transform((gx + off[0], gy + off[1])))
            pb = inv.transform(axb.transData.transform((cs_cx - 0.30, cs_cy + off[1] * .25)))
            fig.add_artist(FancyArrowPatch(pa, pb, arrowstyle="-", color=RED, lw=1.0,
                                           zorder=70, shrinkA=0, shrinkB=0))

    # (c) + theme columns
    cols = [("c", title_c, dem_img)]
    for i, t in enumerate(theme_layers):
        lab = "d" if i == 0 else chr(ord("d") + i)
        cols.append((lab, t["title"], t["img"]))
    geo = {"extent": [lons[0], lons[-1], lats[-1], lats[0]], "origin": "upper"}
    axes_map = {}
    for j, (lab, title, img) in enumerate(cols):
        ax = fig.add_axes([0, 0, 1, 1])
        side = "left" if j == 0 else ("right" if j == ncol - 1 else "none")
        xl, yl = fit(ax, (RX + j * HALF, MAPS_Y, HALF, MAP_H), *ext, lat_mid)
        ax.imshow(img, **geo, zorder=1)
        if j == 0:
            draw_hydro(ax, with_names=True)
        for r in rings:
            xs = [c[0] for c in r]; ys = [c[1] for c in r]
            ax.plot(xs + xs[:1], ys + ys[:1], color="k", lw=1.3, zorder=8)
        if points is not None and j == 0:
            lo = np.atleast_1d(points["lon"]); la = np.atleast_1d(points["lat"])
            ax.scatter(lo, la, s=26, c=RED, marker="o", ec="white", lw=.7, zorder=20)
        ax.set_xticks([t for t in np.arange(106.0, 107.01, 0.3) if xl[0] - .01 <= t <= xl[1] + .01])
        ax.set_yticks([t for t in np.arange(25.6, 26.81, 0.3) if yl[0] - .01 <= t <= yl[1] + .01])
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lon")))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: dms(v, "lat")))
        frame(ax, .7)
        if side != "none":
            ax.spines["right" if side == "left" else "left"].set_visible(False)
        ax.tick_params(labelsize=11.0, length=4.5, direction="in", top=(j == 0),
                       right=(side == "right"), labeltop=(j == 0),
                       labelleft=(side == "left"), labelright=(side == "right"),
                       labelbottom=False, pad=2)
        for t in ax.get_yticklabels():
            t.set_fontweight("bold"); t.set_family(fam); t.set_rotation(90)
            t.set_va("center"); t.set_ha("right" if side == "left" else "left")
        for t in ax.get_xticklabels():
            t.set_fontweight("bold"); t.set_family(fam)
        if j == 0:
            north(ax, xl[0] + .11 * (xl[1] - xl[0]), yl[1] - .245 * (yl[1] - yl[0]),
                  .058 * (yl[1] - yl[0]))
        sbar(ax, xl[0] + .012 * (xl[1] - xl[0]), yl[0] + .022 * (yl[1] - yl[0]), 20)
        letter(ax, lab, title=title, x=0.015, y=0.975, fs=11)
        axes_map[lab] = ax

    # shared legend strip
    lsg = fig.add_axes((RX + .004, STRIP_Y, RW - .008, STRIP_H))
    lsg.set_xticks([]); lsg.set_yticks([]); lsg.set_xlim(0, 1); lsg.set_ylim(0, 1)
    lsg.set_facecolor("white")
    for sp in lsg.spines.values():
        sp.set_visible(False)
    lsg.plot([0, 1], [1, 1], color="#999999", lw=.8, clip_on=False)
    zone = 1.0 / max(1, len(cols))
    lsg.text(.015, .82, "Elevation" if lang == "en" else "高程", fontsize=8.8,
             fontweight="bold", family=fam)
    lsg.imshow(ramp(np.linspace(1, 0, 64)[:, None]), aspect="auto",
               extent=[.015, .032, .10, .62])
    lsg.text(.038, .56, ("high  %d m" if lang == "en" else "高 %d m") % vmax,
             fontsize=7.6, va="center", family=CJK_FALLBACK if lang == "zh" else fam)
    lsg.text(.038, .16, ("low   %d m" if lang == "en" else "低 %d m") % vmin,
             fontsize=7.6, va="center", family=CJK_FALLBACK if lang == "zh" else fam)
    lsg.plot([.150, .180], [.55, .55], color=WATER, lw=1.6)
    lsg.text(.185, .55, "river / stream" if lang == "en" else "河流", fontsize=7.6,
             va="center", family=CJK_FALLBACK if lang == "zh" else fam)
    lsg.plot([.150, .180], [.22, .22], color="k", lw=1.3)
    lsg.text(.185, .22, "county border" if lang == "en" else "县界", fontsize=7.6,
             va="center", family=CJK_FALLBACK if lang == "zh" else fam)
    if points is not None:
        lsg.plot([.150], [.06], marker="o", ms=5, color=RED, mec="white", zorder=5)
        lsg.text(.185, .06, "sampling site (n=%d)" % len(np.atleast_1d(points["lon"])),
                 fontsize=7.6, va="center", family=CJK_FALLBACK if lang == "zh" else fam)
    for i, t in enumerate(theme_layers):
        x0 = zone * (i + 1) + .012
        lsg.text(x0, .86, t["title"], fontsize=8.2, fontweight="bold", family=fam)
        for k, c in enumerate(t["classes"]):
            xx = x0 + (k % 2) * .160
            yy = [.66, .40, .14][k // 2]
            lsg.add_patch(Rectangle((xx, yy - .085), .020, .17, facecolor=c["color"],
                                    edgecolor="#777777", lw=.4))
            lsg.text(xx + .025, yy + .035, "%s %s" % (c["value"], c["label"]),
                     fontsize=6.6, va="center", family=CJK_FALLBACK if lang == "zh" else fam)
            lsg.text(xx + .025, yy - .055, "%.0f km² (%.1f%%)" % t["areas"][k],
                     fontsize=6.4, va="center", color="#444444",
                     family=CJK_FALLBACK if lang == "zh" else fam)
        if t["nodata"] > 0.5:
            lsg.text(x0 + .160, .03, "no data %.1f%%" % t["nodata"], fontsize=6.0,
                     color="#666666", va="center", ha="left", family=fam)
    fig.add_artist(FancyBboxPatch((BLK[0], BLK[1]), BLK[2], BLK[3], boxstyle="square,pad=0",
                                  transform=fig.transFigure, fc="none", ec="k", lw=1.4,
                                  zorder=80))

    # (f-1) borders, (f-2) skeleton, (f-3) terrain stats
    fx = [("f-1", "County border" if lang == "en" else "县界", "shp"),
          ("f-2", "Drainage network (DEM derived)" if lang == "en" else "水系（DEM 骨架）", "riv")]
    if vl is not None or slp is not None:
        fx.append(("f-3", "Terrain statistics" if lang == "en" else "地形统计", "stat"))
    for k, (lab, ttl, kind) in enumerate(fx):
        ax = fig.add_axes([0, 0, 1, 1])
        rx = RX + k * (RW / 3.0)
        if kind == "stat":
            ax.set_position((rx, BOT, RW / 3.0 - .012, FP_H))
            ax.set_xticks([]); ax.set_yticks([]); frame(ax)
            ax.text(.02, .98, ttl, transform=ax.transAxes, fontsize=8.5,
                    fontweight="bold", family=fam, va="top", zorder=20,
                    bbox=dict(boxstyle="square,pad=.22", fc="white", ec="none"))
            letter(ax, lab, x=.98, y=.05, fs=12)
            cell_km2 = (np.cos(np.radians(Y)) * abs(dlon) * 111320.0
                        * abs(dlat) * 110540.0) / 1e6
            w_area = cell_km2[mask]
            e_in = elev[mask]; o = np.argsort(e_in)
            cum = np.cumsum(w_area[o]) / w_area.sum() * 100
            ah = ax.inset_axes([.20, .58, .70, .26])
            ah.plot(e_in[o], cum, color="#b2182b", lw=1.3)
            ah.set_xlim(int(np.floor(e_in.min() / 50) * 50), int(np.ceil(e_in.max() / 50) * 50))
            ah.set_ylim(0, 100); ah.set_yticks([0, 50, 100])
            ah.tick_params(labelsize=6.5, length=2.5)
            ah.set_title("Hypsometric curve" if lang == "en" else "高程—面积曲线",
                         fontsize=7, fontweight="bold", pad=2,
                         family=CJK_FALLBACK if lang == "zh" else fam)
            ah.text(.99, -.30, "mean %.0f m" % e_in.mean(), transform=ah.transAxes,
                    fontsize=6, ha="right", va="top", color="#444444")
            if slp is not None:
                s_in = slp[mask]
                ar = [w_area[(s_in >= SLOPE_BINS[i]) & (s_in < SLOPE_BINS[i + 1])].sum()
                      for i in range(len(SLOPE_LAB))]
                asx = ax.inset_axes([.20, .14, .70, .28])
                bars = asx.bar(range(len(SLOPE_LAB)), ar, color=SLOPE_COL,
                               edgecolor="#555555", lw=.5)
                for b, a_ in zip(bars, ar):
                    asx.text(b.get_x() + b.get_width() / 2, b.get_height(), "%.0f" % a_,
                             ha="center", va="bottom", fontsize=6.2)
                asx.set_xticks(range(len(SLOPE_LAB)))
                asx.set_xticklabels(SLOPE_LAB, fontsize=6.2)
                asx.set_ylim(0, max(ar) * 1.25); asx.set_yticks([])
                asx.tick_params(labelsize=6.2, length=0)
                for sp in ["top", "right", "left"]:
                    asx.spines[sp].set_visible(False)
                asx.set_title("Slope area (km²)" if lang == "en" else "坡度分级面积 (km²)",
                              fontsize=7, fontweight="bold", pad=2,
                              family=CJK_FALLBACK if lang == "zh" else fam)
            continue
        fit(ax, (rx, BOT, RW / 3.0 - .012, FP_H), *ext, lat_mid)
        if kind == "shp":
            if counties_p:
                for f in load_json(counties_p)["features"]:
                    for r in rings_of(f["geometry"]):
                        xs = [c[0] for c in r]; ys = [c[1] for c in r]
                        ax.plot(xs + xs[:1], ys + ys[:1], color="#8c8c8c", lw=.4, zorder=5)
            for r in rings:
                xs = [c[0] for c in r]; ys = [c[1] for c in r]
                ax.plot(xs + xs[:1], ys + ys[:1], color="k", lw=1.2, zorder=6)
        else:
            if vl is not None:
                ax.scatter(sk_lon, sk_lat, s=.04, c="#555555", marker="s",
                           linewidths=0, zorder=4)
                ax.scatter(dp_lon, dp_lat, s=.30, c="k", marker="s",
                           linewidths=0, zorder=5)
            for r in rings:
                xs = [c[0] for c in r]; ys = [c[1] for c in r]
                ax.plot(xs + xs[:1], ys + ys[:1], color="k", lw=1.2, zorder=6)
        ax.set_xticks([]); ax.set_yticks([]); frame(ax)
        ax.text(.02, .98, ttl, transform=ax.transAxes, fontsize=8.5, fontweight="bold",
                family=CJK_FALLBACK if lang == "zh" else fam, va="top", zorder=20,
                bbox=dict(boxstyle="square,pad=.22", fc="white", ec="none"))
        letter(ax, lab, x=.98, y=.05, fs=12)

    os.makedirs(a.out, exist_ok=True)
    base = "%s_%s" % (a.adcode, a.slug or "study-area")
    png = os.path.join(a.out, base + ".png")
    pdf = os.path.join(a.out, base + ".pdf")
    fig.savefig(png, dpi=a.dpi, facecolor="w")
    fig.savefig(pdf, facecolor="w")
    if lang == "en" and riv and not names_map:
        sys.stderr.write("WARNING: --lang en but no --names map; river and reservoir "
                         "labels were skipped rather than printed in Chinese.\n")
    if unmapped:
        print("UNMAPPED hydroonyms (not labelled):", sorted(x for x in unmapped if x))
    print("saved %s\nsaved %s" % (png, pdf))


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prepare", help="download boundaries/DEM and derive layers")
    p1.add_argument("--workdir", required=True)
    p1.add_argument("--adcode", required=True)
    p1.add_argument("--prefecture", required=True)
    p1.add_argument("--province", required=True)
    p1.add_argument("--zoom", type=int, default=11)
    p1.add_argument("--lon-margin", type=float, default=0.28, dest="lon_margin")
    p1.add_argument("--lat-margin", type=float, default=0.22, dest="lat_margin")
    p1.set_defaults(func=cmd_prepare)

    p2 = sub.add_parser("render", help="draw the figure from prepared layers")
    p2.add_argument("--workdir", required=True)
    p2.add_argument("--out", required=True)
    p2.add_argument("--adcode", required=True)
    p2.add_argument("--name", default=None,
                    help="county label; defaults to the name in the county GeoJSON")
    p2.add_argument("--title-c", default=None, dest="title_c",
                    help="title next to panel (c); defaults to --name")
    p2.add_argument("--prefecture", required=True)
    p2.add_argument("--province", required=True)
    p2.add_argument("--province-name", default=None, dest="province_name",
                    help="override; normally resolved from the country file by adcode")
    p2.add_argument("--themes", default=None,
                    help="JSON list of theme rasters; omit for the 4-panel fallback")
    p2.add_argument("--names", default=None, help="JSON map of Chinese to English hydroonyms")
    p2.add_argument("--points", default=None, help="CSV with header lon,lat[,label]")
    p2.add_argument("--lang", choices=["en", "zh"], default="en")
    p2.add_argument("--slug", default=None, help="output filename stem suffix")
    p2.add_argument("--dpi", type=int, default=300)
    p2.set_defaults(func=cmd_render)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
