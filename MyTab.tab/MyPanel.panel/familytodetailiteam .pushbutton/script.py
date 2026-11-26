# -*- coding: utf-8 -*-
__title__ = "Family to\nDetail Item"
__doc__ = "Creates a Detail Item family from the plan geometry of selected families and places it at each family's location."

import os
import re
import tempfile
import time
from pyrevit import revit, DB

doc = revit.doc
app = doc.Application
view = doc.ActiveView

DELETE_ORIGINAL = False


class LoadOpts(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = DB.FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def find_template():
    for root, _, files in os.walk(app.FamilyTemplatePath):
        for f in files:
            if f.lower().endswith(".rft") and "detail item" in f.lower():
                return os.path.join(root, f)
    raise Exception("No Detail Item template found. Check File Locations in Options.")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|{}\[\]]', "_", name)


def collect_curves(geom, out):
    for g in geom:
        if isinstance(g, DB.GeometryInstance):
            collect_curves(g.GetInstanceGeometry(), out)
        elif isinstance(g, DB.Solid):
            for e in g.Edges:
                out.append(e.AsCurve())
        elif isinstance(g, DB.Curve):
            out.append(g)


def flat_lines(curve, z):
    pts = [DB.XYZ(p.X, p.Y, z) for p in curve.Tessellate()]
    lines = []
    for a, b in zip(pts, pts[1:]):
        if a.DistanceTo(b) > 1e-6:
            lines.append(DB.Line.CreateBound(a, b))
    return lines


def instance_point(inst):
    loc = inst.Location
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    return loc.Curve.Evaluate(0.5, True)


def build_family(inst, z, name):
    p = instance_point(inst)
    origin = DB.XYZ(p.X, p.Y, z)

    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    opt.IncludeNonVisibleObjects = False
    geom = inst.get_Geometry(opt)

    raw = []
    if geom:
        collect_curves(geom, raw)

    lines = []
    for c in raw:
        lines.extend(flat_lines(c, z))
    if not lines:
        raise Exception("no plan geometry returned by Revit")

    to_origin = DB.Transform.CreateTranslation(origin.Negate())
    famdoc = app.NewFamilyDocument(find_template())
    path = os.path.join(tempfile.gettempdir(), name + ".rfa")
    try:
        plan = None
        for v in DB.FilteredElementCollector(famdoc).OfClass(DB.ViewPlan):
            if not v.IsTemplate:
                plan = v
                break
        if plan is None:
            raise Exception("no plan view in the Detail Item template")

        t = DB.Transaction(famdoc, "Create detail geometry")
        t.Start()
        added = 0
        first_error = None
        try:
            for l in lines:
                try:
                    famdoc.FamilyCreate.NewDetailCurve(plan, l.CreateTransformed(to_origin))
                    added += 1
                except Exception as ex:
                    if first_error is None:
                        first_error = str(ex)
            if added == 0:
                raise Exception("Revit rejected all {} lines. First error: {}".format(
                    len(lines), first_error))
            t.Commit()
        except Exception:
            t.RollBack()
            raise

        so = DB.SaveAsOptions()
        so.OverwriteExistingFile = True
        famdoc.SaveAs(path, so)
        print("  family saved: {} ({} of {} lines)".format(path, added, len(lines)))
    finally:
        famdoc.Close(False)

    return path, origin


def place(path, origin):
    with revit.Transaction("Place Detail Item from Family"):
        ok, fam = doc.LoadFamily(path, LoadOpts())
        print("  family loaded: {}".format(ok))
        if fam is None:
            raise Exception("family failed to load")
        symbol = doc.GetElement(list(fam.GetFamilySymbolIds())[0])
        if not symbol.IsActive:
            symbol.Activate()
        inst = doc.Create.NewFamilyInstance(origin, symbol, view)
        print("  placed instance id: {}".format(inst.Id))
        return inst


# ---------- main ----------

if view.ViewType not in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.AreaPlan):
    print("Run this from a plan view. Current view type: {}".format(view.ViewType))
    raise SystemExit

families = [e for e in revit.get_selection() if isinstance(e, DB.FamilyInstance)]
print("Selected family instances: {}".format(len(families)))
if not families:
    print("Select the families in this plan view and run again.")
    raise SystemExit

z = view.GenLevel.Elevation if view.GenLevel else 0.0
created = []
done_ids = []

for inst in families:
    print("Processing {} ({})".format(inst.Symbol.FamilyName, inst.Id))
    name = safe_name("DI_{}_{}".format(inst.Symbol.FamilyName, int(time.time() * 1000)))
    try:
        path, origin = build_family(inst, z, name)
        new_inst = place(path, origin)
        created.append(new_inst.Id)
        done_ids.append(inst.Id)
    except Exception as ex:
        print("  skipped: {}".format(ex))

if DELETE_ORIGINAL and done_ids:
    with revit.Transaction("Delete original families"):
        for eid in done_ids:
            doc.Delete(eid)

print("Created {} Detail Item(s).".format(len(created)))