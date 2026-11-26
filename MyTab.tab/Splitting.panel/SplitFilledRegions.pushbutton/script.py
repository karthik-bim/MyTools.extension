# -*- coding: utf-8 -*-
__title__ = "Split\nFilled Regions"
__doc__ = "Splits selected filled regions with separate, unconnected closed loops into one filled region per loop group."

from pyrevit import revit, DB
from System.Collections.Generic import List

doc = revit.doc


# ---------- 2D geometry helpers (XY plane) ----------

def loop_polygon(loop):
    pts = []
    for c in loop:
        for p in c.Tessellate():
            pts.append((p.X, p.Y))
    return pts


def polygon_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def point_in_polygon(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


# ---------- grouping loops into separate regions ----------

def split_loops(loops):
    """
    Returns a list of (outer_index, [hole_indices]).
    Loops at even nesting depth start a new region.
    Loops at odd depth are holes of their parent region.
    """
    polys = [loop_polygon(l) for l in loops]
    areas = [polygon_area(p) for p in polys]
    n = len(loops)

    parent = [None] * n
    for i in range(n):
        px, py = polys[i][0]
        best = None
        for j in range(n):
            if j == i or areas[j] <= areas[i]:
                continue
            if point_in_polygon(px, py, polys[j]):
                if best is None or areas[j] < areas[best]:
                    best = j
        parent[i] = best

    def depth(i):
        d = 0
        p = parent[i]
        while p is not None:
            d += 1
            p = parent[p]
        return d

    depths = [depth(i) for i in range(n)]

    groups = {}
    for i in range(n):
        if depths[i] % 2 == 0:
            groups.setdefault(i, [])
    for i in range(n):
        if depths[i] % 2 == 1:
            groups.setdefault(parent[i], []).append(i)

    return list(groups.items())


# ---------- Revit helpers ----------

def split_region(region):
    loops = list(region.GetBoundaries())
    if len(loops) < 2:
        return 0

    groups = split_loops(loops)
    if len(groups) < 2:
        return 0

    type_id = region.GetTypeId()
    view_id = region.OwnerViewId

    for outer, holes in groups:
        cls = List[DB.CurveLoop]()
        cls.Add(loops[outer])
        for h in holes:
            cls.Add(loops[h])
        DB.FilledRegion.Create(doc, type_id, view_id, cls)

    doc.Delete(region.Id)
    return len(groups)


# ---------- main ----------

regions = [e for e in revit.get_selection() if isinstance(e, DB.FilledRegion)]
if not regions:
    print("No filled regions selected. Select the filled regions and run again.")
    raise SystemExit

split_count = 0
new_count = 0

with revit.Transaction("Split Filled Regions"):
    for r in regions:
        try:
            n = split_region(r)
            if n:
                split_count += 1
                new_count += n
        except Exception as ex:
            print("Region {} skipped: {}".format(r.Id, ex))

print("Split {} filled region(s) into {} new region(s).".format(split_count, new_count))