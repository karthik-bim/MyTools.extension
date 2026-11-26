# -*- coding: utf-8 -*-
__title__ = "Split\nFloors"
__doc__ = "Splits floors whose sketch holds separate, unconnected closed regions into one floor per region."

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
    Loops nested at even depth are outer boundaries of separate regions.
    Loops nested at odd depth are holes belonging to their parent region.
    """
    polys = [loop_polygon(l) for l in loops]
    areas = [polygon_area(p) for p in polys]
    n = len(loops)

    # find the smallest loop that contains each loop
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

    return [(outer, holes) for outer, holes in groups.items()]


# ---------- Revit helpers ----------

def get_floors():
    sel = revit.get_selection()
    floors = [e for e in sel if isinstance(e, DB.Floor)]
    if not floors:
        collector = DB.FilteredElementCollector(doc, doc.ActiveView.Id) \
            .OfCategory(DB.BuiltInCategory.OST_Floors) \
            .WhereElementIsNotElementType()
        floors = list(collector)
    return floors


def floor_loops(floor):
    sketch = doc.GetElement(floor.SketchId)
    loops = []
    for arr in sketch.Profile:
        cl = DB.CurveLoop()
        for c in arr:
            cl.Append(c)
        loops.append(cl)
    return loops


def split_floor(floor):
    loops = floor_loops(floor)
    if len(loops) < 2:
        return 0

    groups = split_loops(loops)
    if len(groups) < 2:
        return 0

    type_id = floor.GetTypeId()
    level_id = floor.LevelId

    offset = None
    p = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
    if p:
        offset = p.AsDouble()

    created = 0
    for outer, holes in groups:
        cls = List[DB.CurveLoop]()
        cls.Add(loops[outer])
        for h in holes:
            cls.Add(loops[h])
        new_floor = DB.Floor.Create(doc, cls, type_id, level_id)
        if offset is not None:
            np = new_floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if np and not np.IsReadOnly:
                np.Set(offset)
        created += 1

    doc.Delete(floor.Id)
    return created


# ---------- main ----------

floors = get_floors()
total_new = 0
total_split = 0

with revit.Transaction("Split Disconnected Floors"):
    for fl in floors:
        try:
            n = split_floor(fl)
            if n:
                total_split += 1
                total_new += n
        except Exception as ex:
            print("Floor {} skipped: {}".format(fl.Id, ex))

print("Split {} floor(s) into {} new floor(s).".format(total_split, total_new))