# -*- coding: utf-8 -*-
"""Pattern Maker: enter a pattern WIDTH and LENGTH in mm, choose straight joints
or running bond (staggered widthwise or lengthwise), and assign the pattern to
filled region types and/or floor types.

Straight joints are created automatically. Running bond needs a PAT file imported
into the project once per size and stagger direction, because Revit's API cannot
import PAT files. The script writes that file and tells you where it is; after the
import, the button assigns the pattern automatically."""

import math
import os

from pyrevit import revit, forms

from Autodesk.Revit.DB import (
    Element, FilteredElementCollector, FillPattern, FillPatternElement,
    FillPatternTarget, FillPatternHostOrientation, FillGrid,
    FilledRegionType, FloorType, Material, UV)
from System.Collections.Generic import List

doc = revit.doc
MM_TO_FT = 1.0 / 304.8  # Revit's API always works in feet internally

LAYOUT_STRAIGHT = "Straight joints"
LAYOUT_RUNNING = "Running bond"
STAGGER_WIDTH = "Widthwise: each row starts half a width further on"
STAGGER_LENGTH = "Lengthwise: each column starts half a length further up"
STAGGER_NAMES = {STAGGER_WIDTH: "Widthwise", STAGGER_LENGTH: "Lengthwise"}
TARGET_FILLS = "Filled regions"
TARGET_FLOORS = "Floors"
TARGET_BOTH = "Both"

PAT_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "PatternMaker")


def el_name(el):
    """Element name. In pyRevit's IronPython, reading el.Name directly fails on
    some Revit objects, so read it through the Element.Name property first."""
    try:
        return Element.Name.GetValue(el)
    except Exception:
        return el.Name


def ask_length(prompt, default_mm):
    raw = forms.ask_for_string(default=str(default_mm), prompt=prompt,
                               title="Pattern Maker")
    if not raw:
        return None
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        forms.alert("'%s' is not a number." % raw)
        return None
    if value <= 0:
        forms.alert("The value must be greater than zero.")
        return None
    return value


def pattern_name(w, l, stagger, which):
    """stagger: None for straight joints, otherwise 'Widthwise' or 'Lengthwise'."""
    if stagger is None:
        layout = "Straight"
    elif stagger == "Widthwise":
        layout = "Running Bond"
    else:
        layout = "Running Bond Lengthwise"
    return "Tile %s %gx%g mm (%s)" % (layout, w, l, which)


def find_pattern(name):
    for fpe in FilteredElementCollector(doc).OfClass(FillPatternElement):
        if el_name(fpe) == name:
            return fpe.Id
    return None


# ---- Straight joints: created directly with the API (solid lines only) ----

def make_solid_grid(angle_deg, spacing_ft):
    # Revit's FillGrid angle is in radians, not degrees
    grid = FillGrid(math.radians(angle_deg), spacing_ft)
    grid.Origin = UV(0.0, 0.0)
    grid.Shift = 0.0
    return grid


def create_straight_pattern(name, target, orientation, w_ft, l_ft):
    grids = List[FillGrid]()
    grids.Add(make_solid_grid(0.0, l_ft))    # horizontal joints
    grids.Add(make_solid_grid(90.0, w_ft))   # vertical joints
    fp = FillPattern(name, target, orientation)
    fp.SetFillGrids(grids)
    return FillPatternElement.Create(doc, fp).Id


# ---- Running bond: written as a PAT file, imported once per size ----

def pat_text(w, l, stagger, target):
    """PAT file text for a running bond pattern. Values are in mm.
    Descriptor format: angle, x-origin, y-origin, shift, spacing, dash, space, ...
    Widthwise: rows of height l, joints every w, every other row shifted by w/2.
    Lengthwise: columns of width w, joints every l, every other column shifted by l/2."""
    name = pattern_name(w, l, stagger, target)
    ptype = "DRAFTING" if target == "Drafting" else "MODEL"
    if stagger == "Widthwise":
        horizontal = "0, 0,0, 0,%g" % l                    # row joints, solid
        vertical = "90, 0,0, %g,%g, %g,%g" % (l, w / 2.0, l, -l)
    else:
        horizontal = "0, 0,0, %g,%g, %g,%g" % (w, l / 2.0, w, -w)
        vertical = "90, 0,0, 0,%g" % w                     # column joints, solid
    return "\n".join([
        ";%UNITS=MM",
        "*%s, Created by Pattern Maker" % name,
        ";%TYPE=" + ptype,
        horizontal,
        vertical,
        "",
    ])


def pat_file_name(w, l, stagger, target):
    tag = "RunningBond" if stagger == "Widthwise" else "RunningBondLengthwise"
    return "Tile_%s_%gx%g_%s.pat" % (tag, w, l, target)


def write_pat(w, l, stagger, target):
    if not os.path.isdir(PAT_FOLDER):
        os.makedirs(PAT_FOLDER)
    path = os.path.join(PAT_FOLDER, pat_file_name(w, l, stagger, target))
    f = open(path, "w")
    try:
        f.write(pat_text(w, l, stagger, target))
    finally:
        f.close()
    return path


# ---- Assignment ----

def floor_type_materials(floor_type):
    """Materials used in the floor type's layers, keyed by name."""
    materials = {}
    cs = floor_type.GetCompoundStructure()
    if cs is None:
        return materials
    for layer in cs.GetLayers():
        mat = doc.GetElement(layer.MaterialId)
        if isinstance(mat, Material):
            materials[el_name(mat)] = mat
    return materials


def main():
    w = ask_length("Pattern WIDTH in mm (across):", 300)
    if w is None:
        return
    l = ask_length("Pattern LENGTH in mm (up):", 600)
    if l is None:
        return

    layout = forms.CommandSwitchWindow.show(
        [LAYOUT_STRAIGHT, LAYOUT_RUNNING], message="Joint layout:")
    if not layout:
        return

    stagger = None
    if layout == LAYOUT_RUNNING:
        choice = forms.CommandSwitchWindow.show(
            [STAGGER_WIDTH, STAGGER_LENGTH], message="Stagger direction:")
        if not choice:
            return
        stagger = STAGGER_NAMES[choice]
    running = stagger is not None

    target = forms.CommandSwitchWindow.show(
        [TARGET_FILLS, TARGET_FLOORS, TARGET_BOTH],
        message="Assign the pattern to:")
    if not target:
        return

    fill_types, floor_types = {}, {}
    chosen_fills, chosen_floors = [], []

    if target in (TARGET_FILLS, TARGET_BOTH):
        fill_types = dict((el_name(t), t) for t in
                          FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements())
        chosen_fills = forms.SelectFromList.show(
            sorted(fill_types.keys()), multiselect=True,
            title="Filled region types to update", button_name="Assign") or []

    if target in (TARGET_FLOORS, TARGET_BOTH):
        floor_types = dict((el_name(t), t) for t in
                           FilteredElementCollector(doc).OfClass(FloorType).ToElements())
        chosen_floors = forms.SelectFromList.show(
            sorted(floor_types.keys()), multiselect=True,
            title="Floor types to update", button_name="Assign") or []

    if not chosen_fills and not chosen_floors:
        return

    w_ft = w * MM_TO_FT
    l_ft = l * MM_TO_FT
    drafting_name = pattern_name(w, l, stagger, "Drafting")
    model_name = pattern_name(w, l, stagger, "Model")
    drafting_id = model_id = None

    # Running bond: the imported pattern must exist first. If it does not,
    # write the PAT file and ask for the one-time import.
    if running:
        missing = []
        if chosen_fills:
            drafting_id = find_pattern(drafting_name)
            if drafting_id is None:
                missing.append(("Drafting (filled regions)", drafting_name,
                                write_pat(w, l, stagger, "Drafting")))
        if chosen_floors:
            model_id = find_pattern(model_name)
            if model_id is None:
                missing.append(("Model (floors)", model_name,
                                write_pat(w, l, stagger, "Model")))
        if missing:
            lines = ["Running bond needs its pattern imported into this project once for this size.",
                     "Then run Pattern Maker again and it will assign automatically.",
                     "",
                     "Manage > Additional Settings > Fill Patterns",
                     "Choose the pattern type, click New, choose Custom, Browse to the file,",
                     "pick the pattern, keep the name and click OK.",
                     ""]
            for kind, name, path in missing:
                lines.append("%s: %s" % (kind, name))
                lines.append("File: %s" % path)
                lines.append("")
            forms.alert("\n".join(lines), title="Pattern Maker")
            return

    changed = {}
    failed = []

    with revit.Transaction("Pattern Maker"):
        if not running:
            # Straight joints: create the pattern now, or reuse it if it already exists
            if chosen_fills:
                drafting_id = find_pattern(drafting_name)
                if drafting_id is None:
                    drafting_id = create_straight_pattern(
                        drafting_name, FillPatternTarget.Drafting,
                        FillPatternHostOrientation.ToView, w_ft, l_ft)
            if chosen_floors:
                model_id = find_pattern(model_name)
                if model_id is None:
                    model_id = create_straight_pattern(
                        model_name, FillPatternTarget.Model,
                        FillPatternHostOrientation.ToHost, w_ft, l_ft)

        if chosen_fills:
            for name in chosen_fills:
                try:
                    fill_types[name].ForegroundPatternId = drafting_id
                except Exception as ex:
                    failed.append("%s: %s" % (name, ex))

        if chosen_floors:
            for name in chosen_floors:
                for mat in floor_type_materials(floor_types[name]).values():
                    try:
                        mat.SurfaceForegroundPatternId = model_id
                        changed[el_name(mat)] = True
                    except Exception as ex:
                        failed.append("%s: %s" % (el_name(mat), ex))

    shown = layout if stagger is None else "%s, staggered %s" % (layout, stagger.lower())
    lines = ["Pattern: %s x %s mm, %s" % (w, l, shown)]
    if chosen_fills:
        lines.append("Filled region types updated: %d" % len(chosen_fills))
    if chosen_floors:
        lines.append("Materials given the floor surface pattern: %s"
                     % (", ".join(sorted(changed.keys())) or "none"))
        lines.append("Note: a material used by walls or other floors changes there too.")
    if failed:
        lines.append("Could not update: " + "; ".join(failed))
    forms.alert("\n".join(lines), title="Pattern Maker")


main()
