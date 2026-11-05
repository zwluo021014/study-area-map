#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clip an OSM waterways / water-polygons shapefile to a county polygon.

Vertex filtering drops channels that merely cross the boundary, so the clip is
done with shapely intersection instead.

  python osm_clip.py --osm ./osm/gis_osm_waterways_free_1.shp \
                     --county ./work/data/522729.json --out ./work/data/rivers.json
                     [--encoding utf-8] [--fclass river,stream,canal]
"""
import argparse, json, io
import shapefile
from shapely.geometry import shape, mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--osm", required=True)
    ap.add_argument("--county", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--encoding", default="utf-8",
                    help="check the .cpg; Geofabrik ships UTF-8 despite the GBK-looking bytes")
    ap.add_argument("--fclass", default="river,stream,canal")
    ap.add_argument("--min-length", type=float, default=1e-4,
                    help="minimum clipped length in degrees (~11 m)")
    a = ap.parse_args()

    poly = shape(json.load(io.open(a.county, encoding="utf-8"))["features"][0]["geometry"])
    keep = set(x.strip() for x in a.fclass.split(","))
    r = shapefile.Reader(a.osm, encoding=a.encoding, encodingErrors="replace")
    fields = [f[0] for f in r.fields][1:]
    ib = poly.bounds
    feats, skipped = [], []
    for shp, rec in zip(r.shapes(), r.records()):
        bb = shp.bbox
        if bb[2] < ib[0] or bb[0] > ib[2] or bb[3] < ib[1] or bb[1] > ib[3]:
            continue
        d = dict(zip(fields, rec))
        if "fclass" in d and str(d["fclass"]) not in keep:
            continue
        cut = shape(shp.__geo_interface__).intersection(poly)
        parts = (list(cut.geoms) if cut.geom_type in ("MultiLineString", "MultiPolygon")
                 else [cut])
        for p in parts:
            if p.is_empty:
                continue
            L = p.length
            if L < a.min_length:
                continue
            nm = str(d.get("name") or "")
            if not nm and str(d.get("fclass")) == "river":
                skipped.append(str(d.get("osm_id")))
            feats.append({"type": "Feature", "geometry": mapping(p),
                          "properties": {"name": nm, "fclass": str(d.get("fclass")),
                                         "osm_id": str(d.get("osm_id")),
                                         "length_deg": L}})
    json.dump({"type": "FeatureCollection", "features": feats},
              io.open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    named = sum(1 for f in feats if f["properties"]["name"])
    print("wrote %s: %d parts, %d named" % (a.out, len(feats), named))
    if skipped:
        print("note: %d unnamed river-class segments kept (no name tag in OSM)" % len(skipped))


if __name__ == "__main__":
    main()
