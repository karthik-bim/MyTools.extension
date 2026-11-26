# -*- coding: utf-8 -*-
"""
3D Family to Detail Item
Pick a 3D .rfa family, choose the side to show, and this creates a 2D Detail Item
family with the visible outline of that side. The 3D family is opened, read,
and closed without saving, so it is not changed.
"""
import clr
clr.AddReference("System.Windows.Forms")

import os

from System.Windows.Forms import DialogResult, OpenFileDialog, SaveFileDialog
from pyrevit import forms
from Autodesk.Revit.DB import (FilteredElementCollector, GeometryInstance, Line, Options,
                               SaveAsOptions, Solid, Transaction, ViewDetailLevel,
                               ViewDrafting, ViewFamily, ViewFamilyType, XYZ)
from Autodesk.Revit.UI import TaskDialog

TITLE = "3D Family to Detail Item"
TOL = 1e-6                  # feet
DOT_TOL = 1e-4              # faces this close to edge-on count as visible
FT_TO_MM = 304.8
TEMPLATE_OVERRIDE = None    # full path to a Detail Item .rft if auto-detect fails

app = __revit__.Application

# side: (direction toward the viewer, screen-x axis, sign, screen-y axis, sign)
VIEWS = {
    "Front":      ((0, -1, 0), "X", 1, "Z", 1),
    "Back":       ((0, 1, 0),  "X", -1, "Z", 1),
    "Right":      ((1, 0, 0),  "Y", 1, "Z", 1),
    "Left":       ((-1, 0, 0), "Y", -1, "Z", 1),
    "Top (plan)": ((0, 0, 1),  "X", 1, "Y", 1),
    "Bottom":     ((0, 0, -1), "X", 1, "Y", -1),
}


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


def get_drafting_view(fam):
    """Return a drafting view in the family, creating one if the template has none."""
    for v in FilteredElementCollector(fam).OfClass(ViewDrafting).ToElements():
        if not v.IsTemplate:
            return v
    for vft in FilteredElementCollector(fam).OfClass(ViewFamilyType).ToElements():
        if vft.ViewFamily == ViewFamily.Drafting:
            return ViewDrafting.Create(fam, vft.Id)
    raise Exception("No drafting view type found in this family template.")


def rkey(p):
    return (round(p.X, 5), round(p.Y, 5), round(p.Z, 5))


def collect_solids(geom, out):
    if geom is None:
        return out
    for g in geom:
        if isinstance(g, GeometryInstance):
            collect_solids(g.GetInstanceGeometry(), out)
        elif isinstance(g, Solid) and g.Volume > 0:
            out.append(g)
    return out


def family_solids(src):
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    opt.IncludeNonVisibleObjects = False
    solids = []
    for el in FilteredElementCollector(src).WhereElementIsNotElementType().ToElements():
        try:
            geom = el.get_Geometry(opt)
        except Exception:
            continue                    # element has no geometry (e.g. reference planes)
        collect_solids(geom, solids)
    return solids


def face_facing(face, pts, v):
    """Dot product of the face normal with the view direction, or None if unknown."""
    try:
        r = face.Project(pts[len(pts) // 2])
        if r is None:
            return None
        n = face.ComputeNormal(r.UVPoint)
        return n.X * v[0] + n.Y * v[1] + n.Z * v[2]
    except Exception:
        return None


def visible_edges(solids, v):
    """Keep edges where at least one adjacent face points toward the viewer or is
    edge-on. Edges where both faces point away from the viewer are hidden."""
    dots, shape = {}, {}
    for s in solids:
        for face in s.Faces:
            for loop in face.EdgeLoops:
                for edge in loop:
                    pts = list(edge.Tessellate())
                    if len(pts) < 2:
                        continue
                    key = frozenset(rkey(p) for p in pts)
                    if key not in shape:
                        shape[key] = pts
                        dots[key] = []
                    dots[key].append(face_facing(face, pts, v))
    keep = []
    for key, ds in dots.items():
        if any(d is None or d > -DOT_TOL for d in ds):
            keep.append(shape[key])
    return keep


def project(p, spec):
    _, ax, sx, ay, sy = spec
    return (sx * getattr(p, ax), sy * getattr(p, ay))


def draw_outline(fam, view, polylines, spec, k):
    fc = fam.FamilyCreate
    flat = [[project(p, spec) for p in pts] for pts in polylines]
    xs = [x for line in flat for x, _ in line]
    ys = [y for line in flat for _, y in line]
    if not xs:
        raise Exception("No visible edges on that side.")
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0

    made = skipped = 0
    for line in flat:
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            a = XYZ((x0 - cx) * k, (y0 - cy) * k, 0.0)
            b = XYZ((x1 - cx) * k, (y1 - cy) * k, 0.0)
            if a.DistanceTo(b) > TOL:
                try:
                    fc.NewDetailCurve(view, Line.CreateBound(a, b))
                    made += 1
                except Exception:
                    skipped += 1
    size = ((max(xs) - min(xs)) * k * FT_TO_MM, (max(ys) - min(ys)) * k * FT_TO_MM)
    return made, skipped, size


def main():
    # 1) Pick the 3D family
    dlg = OpenFileDialog()
    dlg.Filter = "Revit Family (*.rfa)|*.rfa"
    if dlg.ShowDialog() != DialogResult.OK:
        return
    src_path = dlg.FileName
    base = os.path.splitext(os.path.basename(src_path))[0]

    # 2) Side to show
    side = forms.ask_for_one_item(sorted(VIEWS.keys()), default="Front",
                                  prompt="Which side should the 2D symbol show?",
                                  title=TITLE)
    if not side:
        return

    # 3) Drawing scale: 1 keeps real size
    raw = forms.ask_for_string(default="1",
                               prompt="Enter 1 for real size, or N for a 1:N drawing (e.g. 50).",
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

    # 4) Where to save the 2D family
    sdlg = SaveFileDialog()
    sdlg.Filter = "Revit Family (*.rfa)|*.rfa"
    sdlg.FileName = base + "_2D.rfa"
    if sdlg.ShowDialog() != DialogResult.OK:
        return
    out_path = sdlg.FileName

    # 5) Read the 3D family (read-only, closed without saving)
    src = app.OpenDocumentFile(src_path)
    try:
        if not src.IsFamilyDocument:
            raise Exception("That file is not a family.")
        solids = family_solids(src)
        if not solids:
            raise Exception("No solid geometry found in that family.")
        polylines = visible_edges(solids, VIEWS[side][0])
    finally:
        src.Close(False)

    # 6) Build the 2D Detail Item family
    tpl = find_template()
    if not tpl:
        TaskDialog.Show(TITLE, "No Detail Item template found. Set TEMPLATE_OVERRIDE in script.py.")
        return

    fam = app.NewFamilyDocument(tpl)
    try:
        t = Transaction(fam, "Build 2D symbol from 3D family")
        t.Start()
        try:
            view = get_drafting_view(fam)
            made, skipped, size = draw_outline(fam, view, polylines, VIEWS[side], k)
            if made == 0:
                raise Exception("Revit created no lines.")
            t.Commit()
        except Exception:
            t.RollBack()
            raise
        opts = SaveAsOptions()
        opts.OverwriteExistingFile = True
        fam.SaveAs(out_path, opts)
    finally:
        fam.Close(False)

    TaskDialog.Show(TITLE,
        "Saved:\n%s\n\nSide: %s\nEdges used: %d\nDetail lines created: %d\n"
        "Skipped: %d\nPaper size: %.0f x %.0f mm"
        % (out_path, side, len(polylines), made, skipped, size[0], size[1]))


try:
    main()
except Exception as ex:
    TaskDialog.Show(TITLE, "Failed:\n%s" % ex)