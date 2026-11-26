# -*- coding: utf-8 -*-
"""
CAD to Detail Item
Select a DWG/DXF import (or link) in the active view and click this button,
or click it with nothing selected to pick a file. Builds a metric Detail Item
family from the linework and saves it as an .rfa. The project is not changed.
"""
import clr
clr.AddReference("System.Windows.Forms")

import math
import os

from System.Windows.Forms import DialogResult, OpenFileDialog, SaveFileDialog
from pyrevit import forms
from Autodesk.Revit.DB import (Arc, Curve, DWGImportOptions, FilteredElementCollector,
                               GeometryInstance, ImportInstance, Line, Options, PolyLine,
                               SaveAsOptions, Transaction, ViewDrafting, ViewFamily,
                               ViewFamilyType, XYZ)
from Autodesk.Revit.UI import TaskDialog

TITLE = "CAD to Detail Item"
TOL = 1e-6                      # feet, Revit's internal unit
FT_TO_MM = 304.8
TEMPLATE_OVERRIDE = None        # full path to a Detail Item .rft if auto-detect fails

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
app = __revit__.Application


def find_template():
    if TEMPLATE_OVERRIDE and os.path.exists(TEMPLATE_OVERRIDE):
        return TEMPLATE_OVERRIDE
    root = r"C:\ProgramData\Autodesk\RVT %s\Family Templates" % app.VersionNumber
    hits = []
    for folder, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".rft") and "detail item" in f.lower():
                hits.append(os.path.join(folder, f))
    hits.sort(key=lambda p: ("metric" not in p.lower(), len(p)))
    return hits[0] if hits else None


def view_opts(view):
    o = Options()
    o.View = view
    return o


def gather_curves(geom, out):
    """Flatten a Revit geometry element into Curve objects. GetInstanceGeometry()
    already returns project coordinates, so no instance transform is applied."""
    if geom is None:
        return out
    for g in geom:
        if isinstance(g, GeometryInstance):
            gather_curves(g.GetInstanceGeometry(), out)
        elif isinstance(g, PolyLine):
            pts = list(g.GetCoordinates())
            for a, b in zip(pts, pts[1:]):
                if a.DistanceTo(b) > TOL:
                    out.append(Line.CreateBound(a, b))
        elif isinstance(g, Curve):
            out.append(g)
    return out


def point_at(c, t):
    """Point at fraction t (0 to 1) along a curve, or None if it has no finite extent."""
    try:
        if c.IsBound:
            return c.Evaluate(t, True)
        p0, p1 = c.GetEndParameter(0), c.GetEndParameter(1)
        if math.isinf(p0) or math.isinf(p1) or math.isnan(p0) or math.isnan(p1):
            return None
        return c.Evaluate(p0 + (p1 - p0) * t, False)
    except Exception:
        return None


def sampled_lines(c, n=48):
    """Approximate a curve with a polyline sampled along its parameter."""
    pts = [point_at(c, i / float(n)) for i in range(n + 1)]
    if any(p is None for p in pts):
        return []
    return [("line", a, b) for a, b in zip(pts, pts[1:])]


def to_pieces(c):
    """Return ('line', a, b) and ('arc', a, m, b, curve, t0, t1) pieces, or [] if unusable."""
    if isinstance(c, Line):
        try:
            if c.IsBound:
                return [("line", c.GetEndPoint(0), c.GetEndPoint(1))]
        except Exception:
            pass
        return []                           # infinite construction line
    if isinstance(c, Arc):
        try:
            pieces = []
            for t0, t1 in ((0.0, 0.5), (0.5, 1.0)):   # two halves, so full circles work
                a = point_at(c, t0)
                m = point_at(c, (t0 + t1) / 2.0)
                b = point_at(c, t1)
                if a is None or m is None or b is None:
                    raise ValueError("arc has no finite extent")
                pieces.append(("arc", a, m, b, c, t0, t1))
            return pieces
        except Exception:
            pass                            # fall back to tessellation below
    # Ellipses, splines, and any arc that failed above
    try:
        pts = list(c.Tessellate())
        if len(pts) >= 2:
            return [("line", a, b) for a, b in zip(pts, pts[1:])]
    except Exception:
        pass
    return sampled_lines(c)


def bounds(pieces):
    xs, ys = [], []
    for pc in pieces:
        pts = pc[1:4] if pc[0] == "arc" else pc[1:3]
        for p in pts:
            xs.append(p.X)
            ys.append(p.Y)
    return min(xs), min(ys), max(xs), max(ys)


def draw_pieces(fam, view, pieces, k, cx, cy):
    """Create detail curves at paper size, centred on the family origin."""
    fc = fam.FamilyCreate

    def P(p):
        return XYZ((p.X - cx) * k, (p.Y - cy) * k, 0.0)

    def draw_line(a, b):
        pa, pb = P(a), P(b)
        if pa.DistanceTo(pb) > TOL:
            fc.NewDetailCurve(view, Line.CreateBound(pa, pb))
            return 1
        return 0

    made = skipped = 0
    for pc in pieces:
        try:
            if pc[0] == "line":
                made += draw_line(pc[1], pc[2])
            else:
                try:
                    fc.NewDetailCurve(view, Arc.Create(P(pc[1]), P(pc[3]), P(pc[2])))
                    made += 1
                except Exception:
                    # Revit rejected this arc (too small or too flat), so draw it as short lines
                    curve, t0, t1 = pc[4], pc[5], pc[6]
                    steps = 24
                    prev = point_at(curve, t0)
                    for i in range(1, steps + 1):
                        nxt = point_at(curve, t0 + (t1 - t0) * i / float(steps))
                        made += draw_line(prev, nxt)
                        prev = nxt
        except Exception:
            skipped += 1
    return made, skipped


def get_drafting_view(fam):
    """Return a drafting view in the family, creating one if the template has none."""
    for v in FilteredElementCollector(fam).OfClass(ViewDrafting).ToElements():
        if not v.IsTemplate:
            return v
    for vft in FilteredElementCollector(fam).OfClass(ViewFamilyType).ToElements():
        if vft.ViewFamily == ViewFamily.Drafting:
            return ViewDrafting.Create(fam, vft.Id)
    raise Exception("No drafting view type found in this family template.")


def import_dwg_into(fam, view, path):
    """Import a DWG/DXF into the family's drafting view and return the import instance."""
    fam.Import(path, DWGImportOptions(), view)
    found = list(FilteredElementCollector(fam, view.Id).OfClass(ImportInstance).ToElements())
    if not found:
        raise Exception("Revit could not import that file into the family.")
    return found[-1]


def build(fam, curves, dwg_path, k):
    t = Transaction(fam, "Build detail item from CAD")
    t.Start()
    try:
        view = get_drafting_view(fam)
        imp = None
        if dwg_path:
            imp = import_dwg_into(fam, view, dwg_path)
            curves = gather_curves(imp.get_Geometry(view_opts(view)), [])

        pieces, rejected = [], []
        for c in curves:
            got = to_pieces(c)
            if got:
                pieces.extend(got)
            else:
                rejected.append(type(c).__name__)
        if not pieces:
            raise Exception("No usable linework found in the CAD.")

        x0, y0, x1, y1 = bounds(pieces)
        made, skipped = draw_pieces(fam, view, pieces, k, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
        if imp is not None:
            fam.Delete(imp.Id)          # keep the DWG out of the family
        if made == 0:
            raise Exception("Revit rejected every curve in view '%s'." % view.Name)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    return {"made": made, "skipped": skipped, "rejected": rejected,
            "view": str(view.ViewType),
            "w": (x1 - x0) * k * FT_TO_MM, "h": (y1 - y0) * k * FT_TO_MM}


def main():
    # 1) Use the selected CAD import in this view, otherwise pick a file
    selected = [doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds()]
    selected = [e for e in selected if isinstance(e, ImportInstance)]

    curves, dwg_path = None, None
    if selected:
        src = selected[0]
        curves = gather_curves(src.get_Geometry(view_opts(uidoc.ActiveView)), [])
        source_name = os.path.splitext(src.Name or "CAD")[0]
    else:
        dlg = OpenFileDialog()
        dlg.Filter = "CAD drawings (*.dwg;*.dxf)|*.dwg;*.dxf"
        if dlg.ShowDialog() != DialogResult.OK:
            return
        dwg_path = dlg.FileName
        source_name = os.path.splitext(os.path.basename(dwg_path))[0]

    # 2) Drawing scale: 1 keeps the CAD's real size
    raw = forms.ask_for_string(
        default="1",
        prompt="Enter 1 to keep the CAD's exact size.\nEnter N for a 1:N drawing (e.g. 50).",
        title=TITLE)
    if not raw:
        return
    try:
        denom = int(raw.strip().split(":")[-1])
        if denom < 1:
            raise ValueError
    except ValueError:
        TaskDialog.Show(TITLE, "Enter a whole number such as 1 or 50.")
        return
    k = 1.0 / denom

    # 3) Where to save
    sdlg = SaveFileDialog()
    sdlg.Filter = "Revit Family (*.rfa)|*.rfa"
    sdlg.FileName = source_name + ".rfa"
    if sdlg.ShowDialog() != DialogResult.OK:
        return
    out_path = sdlg.FileName

    # 4) Build and save
    tpl = find_template()
    if not tpl:
        TaskDialog.Show(TITLE, "No Detail Item template found. Set TEMPLATE_OVERRIDE in script.py.")
        return

    fam = app.NewFamilyDocument(tpl)
    try:
        r = build(fam, curves, dwg_path, k)
        opts = SaveAsOptions()
        opts.OverwriteExistingFile = True
        fam.SaveAs(out_path, opts)
    finally:
        fam.Close(False)

    rej = ", ".join(sorted(set(r["rejected"]))) or "none"
    TaskDialog.Show(TITLE,
        "Saved:\n%s\n\nView: %s\nDetail curves created: %d\nCurves skipped: %d\n"
        "Unusable CAD entities: %d\nUnusable types: %s\nPaper size: %.0f x %.0f mm"
        % (out_path, r["view"], r["made"], r["skipped"],
           len(r["rejected"]), rej, r["w"], r["h"]))


try:
    main()
except Exception as ex:
    TaskDialog.Show(TITLE, "Failed:\n%s" % ex)