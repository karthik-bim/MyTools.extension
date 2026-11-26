# -*- coding: utf-8 -*-
__title__ = "Split\nToposolids"
__doc__ = "Splits selected toposolids with separate, unconnected closed loops into one toposolid per loop group."

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


def split_loops(loops):
    """
    Returns a list of (outer_index, [hole_indices]).
    Loops at even nesting depth start a new group.
    Loops at odd depth are holes of their parent group.
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

def toposolid_loops(topo):
    sketch = doc.GetElement(topo.SketchId)
    loops = []
    for arr in sketch.Profile:
        cl = DB.CurveLoop()
        for c in arr:
            cl.Append(c)
        loops.append(cl)
    return loops


def bbox_min_z(elem):
    bb = elem.get_BoundingBox(None)
    return bb.Min.Z if bb else None


def split_toposolid(topo):
    loops = toposolid_loops(topo)
    if len(loops) < 2:
        return None

    groups = split_loops(loops)
    if len(groups) < 2:
        return None

    type_id = topo.GetTypeId()
    level_id = topo.LevelId
    orig_min_z = bbox_min_z(topo)

    offset_param = topo.LookupParameter("Height Offset From Level")
    offset = offset_param.AsDouble() if offset_param else None

    # Delete the original first so the new toposolids don't overlap it.
    # The loops are kept in memory, so the geometry is still available.
    doc.Delete(topo.Id)

    new_ids = []
    errors = []
    for outer, holes in groups:
        try:
            cls = List[DB.CurveLoop]()
            cls.Add(loops[outer])
            for h in holes:
                cls.Add(loops[h])
            new_topo = DB.Toposolid.Create(doc, cls, type_id, level_id)

            if offset is not None:
                p = new_topo.LookupParameter("Height Offset From Level")
                if p and not p.IsReadOnly:
                    p.Set(offset)

            if orig_min_z is not None:
                new_min_z = bbox_min_z(new_topo)
                if new_min_z is not None:
                    dz = orig_min_z - new_min_z
                    if abs(dz) > 1e-6:
                        DB.ElementTransformUtils.MoveElement(doc, new_topo.Id, DB.XYZ(0, 0, dz))

            new_ids.append(new_topo.Id)
        except Exception as ex:
            errors.append(str(ex))

    print("  loops: {}, groups: {}, created: {}".format(len(loops), len(groups), len(new_ids)))
    for e in errors:
        print("  group error: {}".format(e))
    return len(new_ids)


# ---------- main ----------

toposolids = [e for e in revit.get_selection() if isinstance(e, DB.Toposolid)]
if not toposolids:
    print("No toposolids selected. Select the toposolids and run again.")
    raise SystemExit

split_count = 0
new_count = 0

with revit.Transaction("Split Toposolids"):
    for t in toposolids:
        print("Processing toposolid {}".format(t.Id))
        try:
            n = split_toposolid(t)
            if n:
                split_count += 1
                new_count += n
        except Exception as ex:
            print("  skipped: {}".format(ex))

print("Split {} toposolid(s) into {} new toposolid(s).".format(split_count, new_count))