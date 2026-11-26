# -*- coding: utf-8 -*-
__title__ = "Delete\nUnused Images"
__doc__ = ("Finds raster images that no view, sheet, legend, schedule or element "
           "parameter uses. Writes a log before anything is deleted.")

import os
import codecs
from pyrevit import forms, script, revit
from Autodesk.Revit.DB import (FilteredElementCollector, ImageType, ImageInstance,
                               Transaction, StorageType)

doc = revit.doc
output = script.get_output()

# Keep False for the first run. The button then writes the log and deletes nothing.
# Set True only after you have read the log and the output window.
ENABLE_DELETE = False

# Image types with any of these words in their name are never deleted.
PROTECTED_NAME_PARTS = []

PROGRESS_EVERY = 5000


def key(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def name_of(el):
    try:
        return el.Name
    except Exception:
        return "(image {0})".format(key(el.Id))


def path_of(img_type):
    try:
        path = img_type.GetImageTypeOptions().Path
        if path:
            return path
    except Exception:
        pass
    return "(no file path - embedded or not available)"


def is_protected(img):
    n = name_of(img).lower()
    return any(p.lower() in n for p in PROTECTED_NAME_PARTS)


def label_for(el, param):
    try:
        cat = el.Category.Name
    except Exception:
        cat = "no category"
    try:
        pname = param.Definition.Name
    except Exception:
        pname = "parameter"
    return "{0} > {1}".format(cat, pname)


def scan_element(el, image_keys, used_keys, found_by):
    # Records every image type this element's parameters point to.
    # Returns how many things could not be read (0 means fully read).
    try:
        params = list(el.Parameters)
    except Exception:
        return 1
    problems = 0
    for p in params:
        try:
            if p.StorageType != StorageType.ElementId:
                continue
            v = p.AsElementId()
        except Exception:
            problems += 1
            continue
        if v is None:
            continue
        k = key(v)
        if k in image_keys:
            used_keys.add(k)
            found_by.setdefault(k, set()).add(label_for(el, p))
    return problems


all_types = list(FilteredElementCollector(doc).OfClass(ImageType).ToElements())
image_keys = set(key(t.Id) for t in all_types)
used_keys = set()
found_by = {}     # image type key -> places that use it
unreadable = 0    # anything that could not be read; blocks deletion

output.print_md("## Raster images in project: %d" % len(all_types))

# 1. Images placed in views, sheets and legends
for inst in FilteredElementCollector(doc).OfClass(ImageInstance).ToElements():
    k = key(inst.GetTypeId())
    used_keys.add(k)
    found_by.setdefault(k, set()).add("placed in a view, sheet or legend")

# 2. Every element type
type_ids = list(FilteredElementCollector(doc).WhereElementIsElementType().ToElementIds())
output.print_md("## Scanning %d element types" % len(type_ids))
for i, eid in enumerate(type_ids, 1):
    el = doc.GetElement(eid)
    if el is None:
        unreadable += 1
    else:
        unreadable += scan_element(el, image_keys, used_keys, found_by)
    if i % PROGRESS_EVERY == 0:
        print("  {0} of {1}".format(i, len(type_ids)))

# 3. Every other element: views, sheets, schedules, families, annotations
inst_ids = list(FilteredElementCollector(doc).WhereElementIsNotElementType().ToElementIds())
output.print_md("## Scanning %d elements (this can take several minutes)" % len(inst_ids))
for i, eid in enumerate(inst_ids, 1):
    el = doc.GetElement(eid)
    if el is None:
        unreadable += 1
    else:
        unreadable += scan_element(el, image_keys, used_keys, found_by)
    if i % PROGRESS_EVERY == 0:
        print("  {0} of {1}".format(i, len(inst_ids)))

# 4. Stop if anything could not be read, because that could hide a use
if unreadable:
    output.print_md("### Stopped. Nothing was deleted.")
    print("{0} element(s) or parameter(s) could not be read, so the check is "
          "incomplete.".format(unreadable))
    forms.alert("The check is incomplete, so nothing was deleted. "
                "See the output window.", title="Delete Unused Images")
    script.exit()

# 5. Sort into used, unused and protected
used = [t for t in all_types if key(t.Id) in used_keys]
unused_all = [t for t in all_types if key(t.Id) not in used_keys]
unused = [t for t in unused_all if not is_protected(t)]
protected = [t for t in unused_all if is_protected(t)]


def show(title, items, reasons=False):
    output.print_md("### " + title)
    for i, img in enumerate(sorted(items, key=name_of), 1):
        print("{0}. {1}".format(i, name_of(img)))
        print("    Path: {0}".format(path_of(img)))
        if reasons:
            used_by = sorted(found_by.get(key(img.Id), []))
            print("    Used by: {0}".format("; ".join(used_by)))


output.print_md("## Result: %d to delete, %d protected, %d used"
                % (len(unused), len(protected), len(used)))
if used:
    show("Used (kept)", used, reasons=True)
if protected:
    show("Unused but protected (kept)", protected)
if unused:
    show("Unused (will be deleted)", unused)

if not unused:
    forms.alert("No unused images to delete.", title="Delete Unused Images")
    script.exit()

# 6. Write the log before anything is deleted
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
if not os.path.isdir(desktop):
    desktop = os.path.expanduser("~")
log_path = os.path.join(desktop, "unused_images_log.txt")

lines = [u"Project: {0}".format(doc.Title),
         u"Unused images: {0}".format(len(unused)),
         u""]
for img in sorted(unused, key=name_of):
    lines.append(u"{0}\t{1}".format(name_of(img), path_of(img)))

try:
    f = codecs.open(log_path, "w", "utf-8")
    try:
        f.write(u"\r\n".join(lines) + u"\r\n")
    finally:
        f.close()
except Exception as ex:
    forms.alert("Could not write the log file, so nothing was deleted.\n\n" + str(ex),
                title="Delete Unused Images")
    script.exit()

output.print_md("Log saved to: " + log_path)

# 7. Dry run stops here
if not ENABLE_DELETE:
    forms.alert("Dry run complete. Nothing was deleted.\n\n"
                "The list is in the output window and the log is on your Desktop.\n\n"
                "To delete, set ENABLE_DELETE = True in the script.",
                title="Delete Unused Images")
    script.exit()

# 8. Confirm, then delete in one transaction
choice = forms.alert(
    "%d unused image(s) will be deleted from this project.\n\n"
    "The log is on your Desktop. The image files on disk are not deleted."
    % len(unused),
    title="Delete Unused Images",
    options=["Delete them", "Cancel"])

if choice != "Delete them":
    script.exit()

deleted = 0
errors = []

t = Transaction(doc, "Delete unused images")
t.Start()
try:
    for img in unused:
        try:
            doc.Delete(img.Id)
            deleted += 1
        except Exception as ex:
            errors.append("{0}: {1}".format(name_of(img), ex))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Deletion failed and was rolled back. Nothing was deleted.\n\n"
                + str(ex), title="Delete Unused Images")
    script.exit()

msg = ("Deleted %d unused image(s).\n\n"
       "Press Ctrl+Z to undo the whole deletion in this session." % deleted)
if errors:
    msg += "\n\n%d could not be deleted. See the output window." % len(errors)
    output.print_md("### Could not delete")
    for e in errors:
        print(e)
forms.alert(msg, title="Delete Unused Images")