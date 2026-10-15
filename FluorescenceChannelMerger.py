"""
Fluorescence Channel Merger & Thresholder
------------------------------------------
Version: 4.0
Author: Owen Faust, Rochester Institute of Technology, June 2026.
Created with the help of Claude AI.

A simple GUI wrapper around the DAPI/GFP/TxRed rename -> merge -> threshold ->
scale-bar -> PNG export pipeline. Handles images with any combination of the
three channels present -- 1, 2, or 3 channels per image, in any combination --
so a folder can mix full 3-channel image sets with DAPI-only, TxRed-only,
or any other partial set. Supports processing multiple source folders in one
run, added either via a file dialog or by dragging folders directly onto the
window. DAPI (Ch1) can use auto-stretch per image (the original behavior) or
a manually set min/max, same as the GFP and TxRed channels. Each channel's
display color is also user-selectable via a color picker (defaults to the
traditional DAPI=blue, GFP=green, TxRed=red mapping).

v3.2 adds Fiji-style cell counting: nuclei are segmented from the raw DAPI
channel (Gaussian blur -> auto/manual threshold -> fill holes -> watershed ->
size/edge filtering, i.e. the usual Fiji "Analyze Particles" recipe), then
every channel is measured inside each nucleus (optionally expanded outward to
approximate the whole cell). Results go to per-image summary and per-cell
CSVs, plus an outline overlay PNG per image for checking the segmentation.

v4.0 is a visual refresh: a Windows 11-style interface (Sun Valley theme) with
light and dark modes that follow the Windows setting, sharp text on scaled
displays, a two-column card layout, hover help on every setting, and a
color-coded log. The image pipeline itself is unchanged from v3.2.

Run with:  python FluorescenceChannelMerger.py
Requires:  numpy, tifffile, pillow, scipy, scikit-image
           (tkinter ships with standard Python)
Optional:  tkinterdnd2 (enables drag-and-drop folder support; the app runs
           fine without it, just using the "Add folder" button instead)
           sv-ttk + darkdetect (modern theme and automatic dark mode; without
           them the app falls back to the plain Tk look)
"""

import os
import re
import sys
import csv
import ctypes
import contextlib
import threading
import concurrent.futures
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont, ImageTk
from scipy import ndimage as ndi
from skimage import feature, filters, measure, segmentation

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser
from tkinter import font as tkfont

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

try:
    import sv_ttk
    SV_TTK_AVAILABLE = True
except ImportError:
    SV_TTK_AVAILABLE = False

try:
    import darkdetect
except ImportError:
    darkdetect = None


# ── PIPELINE (same logic as the CLI script, wrapped as functions) ───────────

def hex_to_rgb(hex_color):
    """Convert a '#rrggbb' string to an (r, g, b) tuple of ints (0-255)."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# ── CELL COUNTING / MEASUREMENT ────────────────────────────────────────────

CHANNEL_NAMES = {"ch1": "DAPI", "ch2": "GFP", "ch3": "TxRed"}

# Auto-threshold methods offered in the GUI (names match Fiji's where possible;
# Fiji's "Default" is a variant of IsoData).
THRESHOLD_METHODS = {
    "Otsu": filters.threshold_otsu,
    "Li": filters.threshold_li,
    "Triangle": filters.threshold_triangle,
    "Default (IsoData)": filters.threshold_isodata,
    "Mean": filters.threshold_mean,
}

SUMMARY_FIELDS = [
    "Image", "Channels", "Threshold_Method", "DAPI_Threshold", "Cell_Count",
    "Image_Area_mm2", "Cells_per_mm2", "Total_Nucleus_Area_um2",
    "Mean_Nucleus_Area_um2", "Nucleus_Area_Fraction",
]
for _name in CHANNEL_NAMES.values():
    SUMMARY_FIELDS += [f"{_name}_Image_Mean", f"{_name}_Background",
                       f"{_name}_Mean_In_Cells", f"{_name}_Mean_of_Cell_Means"]

PER_CELL_FIELDS = ["Image", "Cell", "X_px", "Y_px", "Nucleus_Area_um2",
                   "Circularity", "Cell_Area_um2"]
for _name in CHANNEL_NAMES.values():
    PER_CELL_FIELDS += [f"{_name}_Mean", f"{_name}_IntDen", f"{_name}_Mean_BgSub"]


def segment_nuclei(dapi, pixels_per_um, settings):
    """Segment nuclei from a raw DAPI image, the way the usual Fiji recipe does.

    Returns (labels, threshold): a label image (0 = background, 1..N = nuclei)
    and the intensity threshold actually applied.
    """
    sigma = settings["blur_sigma"]
    blurred = ndi.gaussian_filter(dapi, sigma) if sigma > 0 else dapi

    if settings["threshold_method"] == "Manual":
        threshold = settings["manual_threshold"]
    elif blurred.min() == blurred.max():
        # Completely flat image (blank/saturated frame): nothing to find, and
        # some auto-threshold methods crash on it.
        threshold = float(blurred.max())
    else:
        threshold = float(THRESHOLD_METHODS[settings["threshold_method"]](blurred))

    mask = ndi.binary_fill_holes(blurred > threshold)

    blobs, n_blobs = ndi.label(mask)
    if settings["watershed"] and n_blobs:
        # Split touching nuclei (like Fiji's Process > Binary > Watershed):
        # seed one marker per peak of the distance map, then flood outward.
        # Peaks must be at least ~3/4 of a typical nucleus radius apart, where
        # "typical" is the median blob in this image -- so it adapts to the
        # magnification and doesn't chop elongated nuclei into pieces.
        dist = ndi.distance_transform_edt(mask)
        blob_areas = np.bincount(blobs.ravel())[1:]
        big_enough = blob_areas[blob_areas >= settings["min_area_um2"] * pixels_per_um ** 2]
        typical_radius = np.sqrt(np.median(big_enough if big_enough.size else blob_areas) / np.pi)
        min_distance = max(2, int(round(0.75 * typical_radius)))
        peaks = feature.peak_local_max(dist, min_distance=min_distance,
                                       labels=blobs, exclude_border=False)
        markers = np.zeros(mask.shape, dtype=np.int32)
        markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1)
        labels = segmentation.watershed(-dist, markers, mask=mask)
        # Any blob that got no marker (rare, e.g. tiny specks) would otherwise
        # vanish -- keep it as its own object.
        orphans, n_orphans = ndi.label(mask & (labels == 0))
        if n_orphans:
            labels = np.where(orphans > 0, orphans + labels.max(), labels)
    else:
        labels = blobs

    # Size filter (in um^2, like Fiji's Analyze Particles "Size" field)
    px_per_um2 = pixels_per_um ** 2
    areas = np.bincount(labels.ravel())
    keep = areas >= settings["min_area_um2"] * px_per_um2
    if settings["max_area_um2"] is not None:
        keep &= areas <= settings["max_area_um2"] * px_per_um2
    keep[0] = False

    if settings["exclude_edges"]:
        edge_ids = np.unique(np.concatenate(
            [labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        keep[edge_ids] = False

    labels = np.where(keep[labels], labels, 0)
    labels, _, _ = segmentation.relabel_sequential(labels)
    return labels, threshold


def measure_image(image_name, channel_arrays, pixels_per_um, settings):
    """Count cells on DAPI and measure every channel per cell and per image.

    Returns (summary_row, per_cell_rows, labels). labels is None when the
    image has no DAPI channel (nothing to count, but whole-image channel means
    are still reported).
    """
    um2_per_px = 1.0 / pixels_per_um ** 2
    height, width = next(iter(channel_arrays.values())).shape
    image_area_mm2 = height * width * um2_per_px / 1e6

    summary = {
        "Image": image_name,
        "Channels": "+".join(channel_arrays),
        "Image_Area_mm2": round(image_area_mm2, 6),
    }
    for tag, arr in channel_arrays.items():
        summary[f"{CHANNEL_NAMES[tag]}_Image_Mean"] = round(float(arr.mean()), 3)

    if "ch1" not in channel_arrays:
        return summary, [], None

    labels, threshold = segment_nuclei(channel_arrays["ch1"], pixels_per_um, settings)
    n_cells = int(labels.max())
    ids = np.arange(1, n_cells + 1)

    # GFP/TxRed are often cytoplasmic, so optionally grow each nucleus outward
    # (without merging into neighbours) to approximate the whole cell. DAPI is
    # always measured in the nucleus itself.
    expansion_px = settings["expansion_um"] * pixels_per_um
    cell_labels = segmentation.expand_labels(labels, expansion_px) if expansion_px > 0 else labels

    nucleus_areas = np.bincount(labels.ravel(), minlength=n_cells + 1)[1:] * um2_per_px
    cell_areas = np.bincount(cell_labels.ravel(), minlength=n_cells + 1)[1:] * um2_per_px

    summary.update({
        "Threshold_Method": settings["threshold_method"],
        "DAPI_Threshold": round(threshold, 3),
        "Cell_Count": n_cells,
        "Cells_per_mm2": round(n_cells / image_area_mm2, 3) if image_area_mm2 else "",
        "Total_Nucleus_Area_um2": round(float(nucleus_areas.sum()), 3),
        "Mean_Nucleus_Area_um2": round(float(nucleus_areas.mean()), 3) if n_cells else "",
        "Nucleus_Area_Fraction": round(float((labels > 0).mean()), 5),
    })

    per_cell = []
    for i, region in enumerate(measure.regionprops(labels)):
        circularity = (4 * np.pi * region.area / region.perimeter ** 2) if region.perimeter else 0.0
        row = {
            "Image": image_name,
            "Cell": region.label,
            "X_px": round(region.centroid[1], 2),
            "Y_px": round(region.centroid[0], 2),
            "Nucleus_Area_um2": round(float(nucleus_areas[i]), 3),
            "Circularity": round(min(circularity, 1.0), 4),
        }
        if expansion_px > 0:
            row["Cell_Area_um2"] = round(float(cell_areas[i]), 3)
        per_cell.append(row)

    background_mask = cell_labels == 0
    for tag, arr in channel_arrays.items():
        name = CHANNEL_NAMES[tag]
        region_labels = labels if tag == "ch1" else cell_labels
        background = float(np.median(arr[background_mask])) if background_mask.any() else 0.0
        summary[f"{name}_Background"] = round(background, 3)
        if not n_cells:
            continue

        means = ndi.mean(arr, region_labels, ids)
        sums = ndi.sum(arr, region_labels, ids)
        summary[f"{name}_Mean_In_Cells"] = round(float(arr[region_labels > 0].mean()), 3)
        summary[f"{name}_Mean_of_Cell_Means"] = round(float(np.mean(means)), 3)
        for row, mean, total in zip(per_cell, means, sums):
            row[f"{name}_Mean"] = round(float(mean), 3)
            row[f"{name}_IntDen"] = round(float(total), 1)
            row[f"{name}_Mean_BgSub"] = round(float(mean) - background, 3)

    return summary, per_cell, labels


def save_outline_overlay(dapi, labels, path):
    """Save a grayscale DAPI image with each counted nucleus outlined and
    numbered (matching the Cell column in the per-cell CSV), for QC."""
    lo, hi = np.percentile(dapi, (0.5, 99.5))
    gray = np.clip((dapi - lo) / ((hi - lo) or 1), 0, 1) * 255
    rgb = np.repeat(gray.astype(np.uint8)[..., np.newaxis], 3, axis=2)
    rgb[segmentation.find_boundaries(labels, mode="inner")] = (255, 255, 0)

    pil_img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_img)
    font = ImageFont.load_default()
    for region in measure.regionprops(labels):
        # Label just to the right of the nucleus so it doesn't hide it
        y = region.centroid[0]
        x = region.bbox[3] + 1
        text = str(region.label)
        _, top, _, bottom = draw.textbbox((0, 0), text, font=font)
        draw.text((x, y - (bottom - top) / 2), text, fill=(0, 255, 255), font=font)
    pil_img.save(path)


def run_pipeline(source, pixels_per_um, ch1_auto, ch1_min_setting, ch1_max_setting,
                  ch2_min, ch2_max, ch3_min, ch3_max,
                  ch1_brightness, ch1_color, ch2_color, ch3_color,
                  log, open_output=True, count_settings=None):
    """count_settings: None to skip cell counting, otherwise a dict with
    threshold_method, manual_threshold, blur_sigma, min_area_um2,
    max_area_um2 (None = no limit), watershed, exclude_edges, expansion_um."""
    scale_bar_um = 100
    scale_bar_px = int(pixels_per_um * scale_bar_um)

    # ch1_color/ch2_color/ch3_color are passed in as '#rrggbb' hex strings so
    # they can be logged directly in the CSV; convert to RGB tuples for math.
    ch1_rgb = hex_to_rgb(ch1_color)
    ch2_rgb = hex_to_rgb(ch2_color)
    ch3_rgb = hex_to_rgb(ch3_color)

    source_parent = os.path.dirname(source)
    source_name = os.path.basename(source)
    composite = os.path.join(source_parent, f"{source_name} - composite")
    output = os.path.join(source_parent, f"{source_name} - PNG")
    csv_path = os.path.join(output, "metadata.csv")
    counts_dir = os.path.join(source_parent, f"{source_name} - Cell Counts")

    os.makedirs(composite, exist_ok=True)
    os.makedirs(output, exist_ok=True)
    if count_settings is not None:
        os.makedirs(os.path.join(counts_dir, "outlines"), exist_ok=True)

    log(f"Scale set: {pixels_per_um} px/um -> {scale_bar_um}um scale bar = {scale_bar_px} pixels\n")

    # STEP 1: RENAME
    log("Step 1: Renaming files...")
    for filename in os.listdir(source):
        if not filename.endswith(".tif"):
            continue
        new_name = re.sub(r"(?i)dapi", "ch1", filename)
        new_name = re.sub(r"(?i)gfp", "ch2", new_name)
        new_name = re.sub(r"(?i)txred", "ch3", new_name)
        if new_name != filename:
            os.rename(os.path.join(source, filename), os.path.join(source, new_name))
            log(f"  Renamed: {filename} -> {new_name}")
    log("Step 1 complete.\n")

    # STEP 2: MERGE (handles any combination of ch1/ch2/ch3 present per image)
    log("Step 2: Merging channels...")

    def strip_tags(name):
        for tag in ("ch1", "ch2", "ch3"):
            name = name.replace(tag, "")
        return name

    def save_composite(tags_present, paths_present, filename_for_log):
        arrays = [tifffile.imread(p) for p in paths_present]
        merged = np.stack(arrays, axis=0) if len(arrays) > 1 else arrays[0][np.newaxis, ...]

        stem, ext = os.path.splitext(strip_tags(filename_for_log))
        save_name = f"{stem}__{'-'.join(tags_present)}{ext}"
        tifffile.imwrite(
            os.path.join(composite, save_name),
            merged,
            imagej=True,
            metadata={"axes": "CYX"}
        )

    handled = set()

    # Pass 1: images anchored on ch1 (DAPI) -- pick up ch2/ch3 if present, but don't require them
    for filename in sorted(os.listdir(source)):
        if not filename.endswith(".tif") or "ch1" not in filename:
            continue
        ch1_path = os.path.join(source, filename)
        ch2_path = os.path.join(source, filename.replace("ch1", "ch2"))
        ch3_path = os.path.join(source, filename.replace("ch1", "ch3"))

        tags_present = ["ch1"]
        paths_present = [ch1_path]
        handled.add(filename)

        if os.path.exists(ch2_path):
            tags_present.append("ch2")
            paths_present.append(ch2_path)
            handled.add(os.path.basename(ch2_path))
        if os.path.exists(ch3_path):
            tags_present.append("ch3")
            paths_present.append(ch3_path)
            handled.add(os.path.basename(ch3_path))

        if len(tags_present) == 1:
            log(f"  Single-channel DAPI-only image: {filename}")
        elif "ch2" not in tags_present:
            log(f"  No GFP (ch2) found for: {filename} (merging DAPI + TxRed)")
        elif "ch3" not in tags_present:
            log(f"  No TxRed (ch3) found for: {filename} (merging DAPI + GFP)")

        save_composite(tags_present, paths_present, filename)

    # Pass 2: ch2 (GFP) images with no ch1 counterpart
    for filename in sorted(os.listdir(source)):
        if filename in handled or not filename.endswith(".tif") or "ch2" not in filename:
            continue
        ch2_path = os.path.join(source, filename)
        ch3_path = os.path.join(source, filename.replace("ch2", "ch3"))

        tags_present = ["ch2"]
        paths_present = [ch2_path]
        handled.add(filename)

        if os.path.exists(ch3_path):
            tags_present.append("ch3")
            paths_present.append(ch3_path)
            handled.add(os.path.basename(ch3_path))
            log(f"  No DAPI (ch1) found for: {filename} (merging GFP + TxRed)")
        else:
            log(f"  Single-channel GFP-only image: {filename}")

        save_composite(tags_present, paths_present, filename)

    # Pass 3: ch3 (TxRed) images with no ch1 or ch2 counterpart
    for filename in sorted(os.listdir(source)):
        if filename in handled or not filename.endswith(".tif") or "ch3" not in filename:
            continue
        handled.add(filename)
        log(f"  Single-channel TxRed-only image: {filename}")
        save_composite(["ch3"], [os.path.join(source, filename)], filename)

    log("Step 2 complete.\n")

    # STEP 3: THRESHOLD + SCALE BAR + PNG + METADATA
    log("Step 3: Applying thresholds, adding scale bars, and saving PNGs...")

    BAR_COLOR = (255, 255, 255)
    TEXT_COLOR = (255, 255, 255)
    BAR_HEIGHT = 10
    MARGIN = 20
    FONT_SIZE = 24

    with contextlib.ExitStack() as files:
        csvfile = files.enter_context(open(csv_path, "w", newline=""))
        writer = csv.writer(csvfile)
        if count_settings is not None:
            summary_writer = csv.DictWriter(
                files.enter_context(open(os.path.join(counts_dir, "cell_count_summary.csv"), "w", newline="")),
                fieldnames=SUMMARY_FIELDS, restval="")
            per_cell_writer = csv.DictWriter(
                files.enter_context(open(os.path.join(counts_dir, "per_cell_measurements.csv"), "w", newline="")),
                fieldnames=PER_CELL_FIELDS, restval="")
            summary_writer.writeheader()
            per_cell_writer.writeheader()

        writer.writerow([
            "Filename", "Channels", "Width", "Height", "PixelsPerUm",
            "Ch1_Mode", "Ch1_Min", "Ch1_Max", "Ch1_Color",
            "Ch2_Min", "Ch2_Max", "Ch2_Color",
            "Ch3_Min", "Ch3_Max", "Ch3_Color"
        ])

        for filename in sorted(os.listdir(composite)):
            if not filename.endswith(".tif"):
                continue

            # Recover which channels are present from the "__ch1-ch2-ch3" suffix
            # written during Step 2.
            stem, _ = os.path.splitext(filename)
            if "__" in stem:
                tag_part = stem.rsplit("__", 1)[1]
                tags_present = tag_part.split("-")
            else:
                # Fallback for unexpected filenames: assume channels are in
                # ch1, ch2, ch3 order based on however many are in the file.
                tags_present = ["ch1", "ch2", "ch3"]

            img = tifffile.imread(os.path.join(composite, filename))
            if img.ndim == 2:
                img = img[np.newaxis, ...]
            tags_present = tags_present[:img.shape[0]]

            channel_arrays = {tag: img[i].astype(np.float32) for i, tag in enumerate(tags_present)}
            height, width = next(iter(channel_arrays.values())).shape

            rgb_float = np.zeros((height, width, 3), dtype=np.float32)

            # Ch1 (DAPI) -- auto-stretch per image, or manual min/max
            if "ch1" in channel_arrays:
                ch1 = channel_arrays["ch1"]
                if ch1_auto:
                    ch1_min, ch1_max = ch1.min(), ch1.max()
                    ch1_norm = np.clip((ch1 - ch1_min) / (ch1_max - ch1_min), 0, 1)
                    ch1_norm = np.clip(ch1_norm * ch1_brightness, 0, 1)
                else:
                    ch1_min, ch1_max = ch1_min_setting, ch1_max_setting
                    ch1_norm = np.clip((ch1 - ch1_min) / (ch1_max - ch1_min), 0, 1)
                rgb_float += ch1_norm[..., np.newaxis] * np.array(ch1_rgb, dtype=np.float32)
            else:
                ch1_min = ch1_max = None

            # Ch2 (GFP) -- manual threshold
            if "ch2" in channel_arrays:
                ch2 = channel_arrays["ch2"]
                ch2_norm = np.clip((ch2 - ch2_min) / (ch2_max - ch2_min), 0, 1)
                rgb_float += ch2_norm[..., np.newaxis] * np.array(ch2_rgb, dtype=np.float32)

            # Ch3 (TxRed) -- manual threshold
            if "ch3" in channel_arrays:
                ch3 = channel_arrays["ch3"]
                ch3_norm = np.clip((ch3 - ch3_min) / (ch3_max - ch3_min), 0, 1)
                rgb_float += ch3_norm[..., np.newaxis] * np.array(ch3_rgb, dtype=np.float32)

            rgb = np.clip(rgb_float, 0, 255).astype(np.uint8)
            pil_img = Image.fromarray(rgb)
            draw = ImageDraw.Draw(pil_img)

            bar_x1 = width - MARGIN - scale_bar_px
            bar_x2 = width - MARGIN
            bar_y1 = height - MARGIN - BAR_HEIGHT
            bar_y2 = height - MARGIN
            draw.rectangle([bar_x1, bar_y1, bar_x2, bar_y2], fill=BAR_COLOR)

            label = f"{scale_bar_um} um"
            try:
                font = ImageFont.truetype("arial.ttf", FONT_SIZE)
            except Exception:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), label, font=font)
            text_width = bbox[2] - bbox[0]
            text_x = bar_x1 + (scale_bar_px - text_width) // 2
            text_y = bar_y1 - FONT_SIZE - 4
            draw.text((text_x, text_y), label, fill=TEXT_COLOR, font=font)

            # Use the clean base name (without the __ch tag suffix) for the PNG
            png_stem = stem.rsplit("__", 1)[0] if "__" in stem else stem
            png_name = f"{png_stem}.png"
            pil_img.save(os.path.join(output, png_name))

            writer.writerow([
                filename, "+".join(tags_present), width, height, pixels_per_um,
                ("auto" if ch1_auto else "manual") if "ch1" in channel_arrays else "",
                ch1_min if "ch1" in channel_arrays else "",
                ch1_max if "ch1" in channel_arrays else "",
                ch1_color if "ch1" in channel_arrays else "",
                ch2_min if "ch2" in channel_arrays else "",
                ch2_max if "ch2" in channel_arrays else "",
                ch2_color if "ch2" in channel_arrays else "",
                ch3_min if "ch3" in channel_arrays else "",
                ch3_max if "ch3" in channel_arrays else "",
                ch3_color if "ch3" in channel_arrays else "",
            ])
            log(f"  Processed: {filename}  ({'+'.join(tags_present)})")

            # Cell counting + intensities, measured on the RAW pixel values
            # (not the thresholded display colors above).
            if count_settings is not None:
                summary, per_cell, labels = measure_image(
                    png_stem, channel_arrays, pixels_per_um, count_settings)
                summary_writer.writerow(summary)
                per_cell_writer.writerows(per_cell)
                if labels is None:
                    log("    No DAPI channel -- measured whole-image intensities only")
                else:
                    save_outline_overlay(channel_arrays["ch1"], labels,
                                         os.path.join(counts_dir, "outlines", f"{png_stem}_outlines.png"))
                    log(f"    Counted {summary['Cell_Count']} cells "
                        f"(DAPI threshold {summary['DAPI_Threshold']:g})")

    log("Step 3 complete.")
    log(f"\nAll done. Metadata saved to:\n{csv_path}")
    if count_settings is not None:
        log(f"Cell counts and intensities saved to:\n{counts_dir}")

    if open_output:
        try:
            os.startfile(output)
        except Exception:
            pass  # not on Windows, or folder can't be opened automatically

    return output


# ── GUI ───────────────────────────────────────────────────────────────────

APP_VERSION = "4.0"
ICON_FILE = "FluorescenceChannelMerger.ico"

# Colors for the few classic Tk widgets (folder list, log, tooltips) that the
# Sun Valley theme doesn't style itself -- matched to the theme's own colors.
PALETTES = {
    "dark": {
        "bg": "#1c1c1c", "surface": "#232323", "fg": "#fafafa", "muted": "#9e9e9e",
        "border": "#3a3a3a", "accent": "#57c8ff", "accent_fg": "#1c1c1c",
        "ok": "#6ccb5f", "warn": "#fce100", "error": "#ff99a4",
    },
    "light": {
        "bg": "#fafafa", "surface": "#ffffff", "fg": "#1c1c1c", "muted": "#616161",
        "border": "#d9d9d9", "accent": "#0560b6", "accent_fg": "#ffffff",
        "ok": "#0f7b0f", "warn": "#9d5d00", "error": "#c42b1c",
    },
}

# Hover help shown on the (i) icons next to each setting.
HELP = {
    "scale": "Pixels per micron for your objective and camera. Sets the 100 µm "
             "scale bar and converts all areas to µm². Leica 10x = 0.885.",
    "channels": "Min / Max are the raw intensities shown as black and as full color "
                "in the PNGs. They only affect the pictures, never the measurements. "
                "Click a color dot to change that channel's display color.",
    "ch1_auto": "On: DAPI is stretched to its own darkest/brightest pixel in every "
                "image, then multiplied by the brightness factor. Off: use a fixed "
                "Min / Max for every image, like GFP and TxRed.",
    "count": "Find nuclei in the DAPI channel, count them, and measure every channel "
             "per image and per cell. Results go to '<folder> - Cell Counts'.",
    "threshold": "How nucleus pixels are told apart from background. Otsu is a good "
                 "default; Li and Triangle are more inclusive (catch dimmer nuclei). "
                 "Manual uses the raw DAPI value you type for every image.",
    "blur": "Gaussian blur on DAPI before thresholding, to smooth noise and grainy "
            "texture. Raise it if single nuclei break into pieces; 0 = off.",
    "expansion": "Grow each nucleus outward by this many µm (without overlapping its "
                 "neighbours) and measure GFP/TxRed there, to capture cytoplasmic "
                 "signal. DAPI is always measured in the nucleus. 0 = nucleus only.",
    "size": "Objects smaller than Min (debris, specks) or larger than Max (clumps) "
            "are ignored. In µm², using your pixels-per-micron. Leave Max blank for "
            "no upper limit.",
    "watershed": "Split touching or overlapping nuclei so they count separately. "
                 "Turn off if single nuclei are being cut in half.",
    "edges": "Ignore nuclei cut off by the image border. Better per-cell numbers, "
             "but the total count will be a little lower.",
}


def resource_path(name):
    """Path to a file shipped next to the script, or inside the PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def enable_dpi_awareness():
    """Ask Windows not to bitmap-stretch the window on scaled (125%/150%)
    displays, which makes all text blurry. Must run before the window exists."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass  # not Windows, or too old -- harmless


def set_title_bar_theme(window, dark):
    """Make the Windows 10/11 title bar match the app's light/dark mode."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class Tooltip:
    """Small help bubble that appears when hovering over a widget."""

    def __init__(self, app, widget, text):
        self.app, self.widget, self.text = app, widget, text
        self.window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._after_id = self.widget.after(350, self._show)

    def _show(self):
        if self.window is not None:
            return
        p = self.app.palette
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + self.app.px(6)
        y = self.widget.winfo_rooty() - self.app.px(4)
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.window, text=self.text, justify="left", wraplength=self.app.px(300),
            bg=p["surface"], fg=p["fg"], font=("Segoe UI", 9),
            padx=self.app.px(10), pady=self.app.px(7),
            highlightthickness=1, highlightbackground=p["border"], bd=0,
        ).pack()

    def _hide(self, _event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


_BaseWindow = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class App(_BaseWindow):
    def __init__(self):
        super().__init__()
        self.withdraw()  # build everything first, then show -- avoids a flicker
        self.title("Fluorescence Channel Merger")

        # Tk reports how many pixels a point is; 96 dpi (100% scaling) = 1.333.
        self._scale = float(self.tk.call("tk", "scaling")) / (96 / 72)

        icon_path = resource_path(ICON_FILE)
        self._icon_image = None
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(default=icon_path)
                header_icon = Image.open(icon_path).convert("RGBA")
                header_icon = header_icon.resize((self.px(36), self.px(36)), Image.LANCZOS)
                self._icon_image = ImageTk.PhotoImage(header_icon)
            except Exception:
                pass

        self.source_folders = []  # list of folder paths queued for processing
        self.pixels_per_um = tk.StringVar(value="0.885")
        self.ch2_min = tk.StringVar(value="111")
        self.ch2_max = tk.StringVar(value="1108")
        self.ch3_min = tk.StringVar(value="100")
        self.ch3_max = tk.StringVar(value="1000")
        self.ch1_brightness = tk.StringVar(value="2")
        self.ch1_auto_var = tk.BooleanVar(value=True)
        self.ch1_min = tk.StringVar(value="0")
        self.ch1_max = tk.StringVar(value="500")

        # Channel colors (hex strings), default to the traditional DAPI=blue,
        # GFP=green, TxRed=red mapping used in earlier versions.
        self.ch1_color = tk.StringVar(value="#0000ff")
        self.ch2_color = tk.StringVar(value="#00ff00")
        self.ch3_color = tk.StringVar(value="#ff0000")

        # Cell counting (DAPI segmentation) settings
        self.count_enabled = tk.BooleanVar(value=True)
        self.count_method = tk.StringVar(value="Otsu")
        self.count_manual_threshold = tk.StringVar(value="500")
        self.count_blur = tk.StringVar(value="2")
        self.count_min_area = tk.StringVar(value="20")
        self.count_max_area = tk.StringVar(value="")
        self.count_watershed = tk.BooleanVar(value=True)
        self.count_exclude_edges = tk.BooleanVar(value=False)
        self.count_expansion = tk.StringVar(value="0")

        system_dark = darkdetect.isDark() if darkdetect is not None else False
        self.dark_mode = tk.BooleanVar(value=bool(system_dark))
        self.status_text = tk.StringVar(value="Ready")
        self.folder_count_text = tk.StringVar()

        self._swatches = []  # (label widget, color var) pairs, redrawn on theme change
        mono_families = set(tkfont.families())
        self._mono_font = ("Cascadia Mono" if "Cascadia Mono" in mono_families else "Consolas", 9)

        self._apply_theme()
        self._build_header()
        self._build_body()
        self._apply_palette()
        self._update_ch1_mode_state()
        self._update_count_state()
        self._update_folder_count()

        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), min(self.winfo_reqheight(), self.px(640)))
        self.deiconify()
        set_title_bar_theme(self, self.dark_mode.get())

    # ── theme ──────────────────────────────────────────────────────────────

    def px(self, n):
        """Scale a layout size given for a 100%-scaled display."""
        return int(round(n * self._scale))

    @property
    def palette(self):
        return PALETTES["dark" if self.dark_mode.get() else "light"]

    def _apply_theme(self):
        if SV_TTK_AVAILABLE:
            sv_ttk.set_theme("dark" if self.dark_mode.get() else "light")
            # Sun Valley sizes its fonts in screen pixels, which comes out tiny
            # on scaled displays; switch them to point sizes so they scale.
            for name, size in (("SunValleyCaptionFont", 9), ("SunValleyBodyFont", 10),
                               ("SunValleyBodyStrongFont", 10), ("SunValleyBodyLargeFont", 12),
                               ("SunValleySubtitleFont", 14), ("SunValleyTitleFont", 18),
                               ("SunValleyTitleLargeFont", 24), ("SunValleyDisplayFont", 36)):
                try:
                    tkfont.nametofont(name).configure(size=size)
                except tk.TclError:
                    pass

        p = self.palette
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 17))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground=p["muted"])
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 12))
        style.configure("Muted.TLabel", foreground=p["muted"])
        style.configure("Caption.TLabel", font=("Segoe UI", 9), foreground=p["muted"])
        style.configure("Info.TLabel", font=("Segoe UI", 10), foreground=p["muted"])
        style.configure("Status.TLabel", foreground=p["muted"])
        style.configure("StatusOk.TLabel", foreground=p["ok"])
        style.configure("StatusError.TLabel", foreground=p["error"])
        style.configure("Run.Accent.TButton", font=("Segoe UI Semibold", 11),
                        padding=(self.px(22), self.px(8)))

    def _apply_palette(self):
        """Recolor the classic Tk widgets the theme doesn't reach."""
        p = self.palette
        shared = dict(bg=p["surface"], fg=p["fg"], highlightbackground=p["border"],
                      highlightcolor=p["accent"], selectbackground=p["accent"],
                      selectforeground=p["accent_fg"])
        self.folder_listbox.config(**shared)
        self.folder_placeholder.config(bg=p["surface"], fg=p["muted"])
        self.log_text.config(insertbackground=p["fg"], **shared)
        self.log_text.tag_configure("error", foreground=p["error"])
        self.log_text.tag_configure("ok", foreground=p["ok"])
        self.log_text.tag_configure("warn", foreground=p["warn"])
        self.log_text.tag_configure("dim", foreground=p["muted"])
        self.log_text.tag_configure("step", font=(self._mono_font[0], self._mono_font[1], "bold"))
        for swatch, color_var in self._swatches:
            self._draw_swatch(swatch, color_var.get())

    def toggle_dark_mode(self):
        self._apply_theme()
        self._apply_palette()
        self._set_status(self.status_text.get(), self._status_kind)
        set_title_bar_theme(self, self.dark_mode.get())

    # ── layout helpers ─────────────────────────────────────────────────────

    def _card(self, parent, title, help_key=None):
        """A rounded panel with a heading. Returns (card, header_frame)."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(self.px(16), self.px(12)))
        header = ttk.Frame(card)
        header.grid(row=0, column=0, columnspan=6, sticky="ew", pady=(0, self.px(10)))
        ttk.Label(header, text=title, style="Heading.TLabel").pack(side="left")
        if help_key:
            self._info_icon(header, help_key).pack(side="left", padx=(self.px(6), 0))
        return card, header

    def _info_icon(self, parent, help_key):
        icon = ttk.Label(parent, text="ⓘ", style="Info.TLabel", cursor="hand2")
        Tooltip(self, icon, HELP[help_key])
        return icon

    def _setting_label(self, parent, text, help_key=None):
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=text).pack(side="left")
        if help_key:
            self._info_icon(frame, help_key).pack(side="left", padx=(self.px(4), 0))
        return frame

    def _entry(self, parent, variable, width=8):
        return ttk.Entry(parent, textvariable=variable, width=width, justify="right")

    def _make_swatch(self, parent, color_var, label):
        swatch = ttk.Label(parent, cursor="hand2")
        swatch.bind("<Button-1>", lambda e: self.pick_color(color_var, swatch, label))
        Tooltip(self, swatch, f"{label} display color — click to change")
        self._swatches.append((swatch, color_var))
        self._draw_swatch(swatch, color_var.get())
        return swatch

    def _draw_swatch(self, swatch, hex_color):
        # Drawn at 4x and scaled down so the rounded edge is smooth.
        size = self.px(22)
        big = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.rounded_rectangle([2, 2, size * 4 - 3, size * 4 - 3], radius=size,
                               fill=hex_color, outline=self.palette["border"], width=6)
        swatch.image = ImageTk.PhotoImage(big.resize((size, size), Image.LANCZOS))
        swatch.config(image=swatch.image)

    # ── layout ─────────────────────────────────────────────────────────────

    def _build_header(self):
        header = ttk.Frame(self, padding=(self.px(24), self.px(18), self.px(24), self.px(14)))
        header.pack(fill="x")
        if self._icon_image is not None:
            ttk.Label(header, image=self._icon_image).pack(side="left", padx=(0, self.px(12)))
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text="Fluorescence Channel Merger", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=f"Merge · threshold · count cells   —   Gaborski Lab · v{APP_VERSION}",
                  style="Subtitle.TLabel").pack(anchor="w")
        ttk.Checkbutton(header, text="Dark mode", variable=self.dark_mode,
                        style="Switch.TCheckbutton", command=self.toggle_dark_mode
                        ).pack(side="right")

    def _build_body(self):
        body = ttk.Frame(self, padding=(self.px(24), 0, self.px(24), self.px(20)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(16)))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        self._build_scale_card(left).pack(fill="x", pady=(0, self.px(12)))
        self._build_channels_card(left).pack(fill="x", pady=(0, self.px(12)))
        self._build_count_card(left).pack(fill="x")

        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self._build_folders_card(right).grid(row=0, column=0, sticky="nsew", pady=(0, self.px(12)))
        self._build_run_row(right).grid(row=1, column=0, sticky="ew", pady=(0, self.px(12)))
        self._build_log_card(right).grid(row=2, column=0, sticky="nsew")

    def _build_scale_card(self, parent):
        card, _ = self._card(parent, "Microscope", "scale")
        ttk.Label(card, text="Pixels per micron").grid(row=1, column=0, sticky="w")
        self._entry(card, self.pixels_per_um, 10).grid(row=1, column=1, padx=self.px(10))
        ttk.Label(card, text="Leica 10x = 0.885", style="Caption.TLabel").grid(row=1, column=2, sticky="w")
        return card

    def _build_channels_card(self, parent):
        card, _ = self._card(parent, "Channels", "channels")
        gap = {"padx": (self.px(10), 0), "pady": self.px(4)}

        # Min/Max column headings: one pair above DAPI (only shown in manual
        # mode, when DAPI actually has min/max fields) and one above GFP/TxRed.
        self.ch1_headings = [
            ttk.Label(card, text=text, style="Caption.TLabel") for text in ("Min", "Max")]
        for column, heading in zip((2, 3), self.ch1_headings):
            heading.grid(row=1, column=column, sticky="w", padx=gap["padx"])
        for column, text in ((2, "Min"), (3, "Max")):
            ttk.Label(card, text=text, style="Caption.TLabel").grid(
                row=5, column=column, sticky="w", padx=gap["padx"])

        # DAPI: either a brightness factor (auto-stretch) or a fixed min/max
        self._make_swatch(card, self.ch1_color, "DAPI (Ch1)").grid(row=2, column=0, padx=(0, self.px(10)))
        ttk.Label(card, text="DAPI", style="Heading.TLabel").grid(row=2, column=1, sticky="w")
        self.ch1_min_entry = self._entry(card, self.ch1_min)
        self.ch1_min_entry.grid(row=2, column=2, **gap)
        self.ch1_max_entry = self._entry(card, self.ch1_max)
        self.ch1_max_entry.grid(row=2, column=3, **gap)
        self.ch1_brightness_frame = ttk.Frame(card)
        self.ch1_brightness_frame.grid(row=2, column=2, columnspan=2, sticky="w", **gap)
        ttk.Label(self.ch1_brightness_frame, text="Brightness ×").pack(side="left")
        self._entry(self.ch1_brightness_frame, self.ch1_brightness, 6).pack(side="left", padx=(self.px(8), 0))

        auto_row = ttk.Frame(card)
        auto_row.grid(row=3, column=1, columnspan=3, sticky="w", pady=(0, self.px(4)))
        ttk.Checkbutton(auto_row, text="Auto-stretch each image", variable=self.ch1_auto_var,
                        style="Switch.TCheckbutton", command=self._update_ch1_mode_state).pack(side="left")
        self._info_icon(auto_row, "ch1_auto").pack(side="left", padx=(self.px(4), 0))

        ttk.Separator(card).grid(row=4, column=0, columnspan=4, sticky="ew", pady=self.px(8))

        for row, name, label, color_var, min_var, max_var in (
                (6, "GFP", "GFP (Ch2)", self.ch2_color, self.ch2_min, self.ch2_max),
                (7, "TxRed", "TxRed (Ch3)", self.ch3_color, self.ch3_min, self.ch3_max)):
            self._make_swatch(card, color_var, label).grid(row=row, column=0, padx=(0, self.px(10)))
            ttk.Label(card, text=name, style="Heading.TLabel").grid(row=row, column=1, sticky="w")
            self._entry(card, min_var).grid(row=row, column=2, **gap)
            self._entry(card, max_var).grid(row=row, column=3, **gap)
        return card

    def _build_count_card(self, parent):
        card, header = self._card(parent, "Cell counting", "count")
        ttk.Checkbutton(header, variable=self.count_enabled, style="Switch.TCheckbutton",
                        command=self._update_count_state).pack(side="right")
        pad = {"pady": self.px(4)}
        right_pad = {"padx": (self.px(10), self.px(18)), "pady": self.px(4)}

        self._setting_label(card, "Threshold", "threshold").grid(row=1, column=0, sticky="w", **pad)
        self.count_method_combo = ttk.Combobox(
            card, textvariable=self.count_method, state="readonly", width=15,
            values=list(THRESHOLD_METHODS) + ["Manual"])
        self.count_method_combo.grid(row=1, column=1, columnspan=2, sticky="w", **right_pad)
        self.count_method_combo.bind("<<ComboboxSelected>>", lambda e: self._update_count_state())
        self.count_manual_label = ttk.Label(card, text="Value")
        self.count_manual_label.grid(row=1, column=3, sticky="w", **pad)
        self.count_manual_entry = self._entry(card, self.count_manual_threshold)
        self.count_manual_entry.grid(row=1, column=4, sticky="w", padx=(self.px(10), 0), **pad)

        self._setting_label(card, "Blur σ (px)", "blur").grid(row=2, column=0, sticky="w", **pad)
        self.count_blur_entry = self._entry(card, self.count_blur)
        self.count_blur_entry.grid(row=2, column=1, sticky="w", **right_pad)
        self._setting_label(card, "Expand (µm)", "expansion").grid(row=2, column=3, sticky="w", **pad)
        self.count_expansion_entry = self._entry(card, self.count_expansion)
        self.count_expansion_entry.grid(row=2, column=4, sticky="w", padx=(self.px(10), 0), **pad)

        self._setting_label(card, "Size min (µm²)", "size").grid(row=3, column=0, sticky="w", **pad)
        self.count_min_entry = self._entry(card, self.count_min_area)
        self.count_min_entry.grid(row=3, column=1, sticky="w", **right_pad)
        ttk.Label(card, text="Max (µm²)").grid(row=3, column=3, sticky="w", **pad)
        self.count_max_entry = self._entry(card, self.count_max_area)
        self.count_max_entry.grid(row=3, column=4, sticky="w", padx=(self.px(10), 0), **pad)

        switches = ttk.Frame(card)
        switches.grid(row=4, column=0, columnspan=5, sticky="w", pady=(self.px(8), 0))
        self.count_watershed_check = ttk.Checkbutton(
            switches, text="Split touching nuclei", variable=self.count_watershed,
            style="Switch.TCheckbutton")
        self.count_watershed_check.pack(side="left")
        self._info_icon(switches, "watershed").pack(side="left", padx=(self.px(4), self.px(20)))
        self.count_edges_check = ttk.Checkbutton(
            switches, text="Skip edge nuclei", variable=self.count_exclude_edges,
            style="Switch.TCheckbutton")
        self.count_edges_check.pack(side="left")
        self._info_icon(switches, "edges").pack(side="left", padx=(self.px(4), 0))
        return card

    def _build_folders_card(self, parent):
        card, header = self._card(parent, "Source folders")
        ttk.Label(header, textvariable=self.folder_count_text, style="Caption.TLabel").pack(
            side="left", padx=(self.px(10), 0), pady=(self.px(3), 0))
        self.add_button = ttk.Button(header, text="＋  Add folder", command=self.add_folder)
        self.add_button.pack(side="right")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        list_frame = ttk.Frame(card)
        list_frame.grid(row=1, column=0, columnspan=6, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.folder_listbox = tk.Listbox(
            list_frame, height=5, width=48, selectmode="extended", activestyle="none",
            relief="flat", bd=0, highlightthickness=1, font=("Segoe UI", 10))
        self.folder_listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.folder_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(self.px(4), 0))
        self.folder_listbox.config(yscrollcommand=scrollbar.set)

        hint = ("Drag folders here, or click  ＋ Add folder" if DND_AVAILABLE
                else "Click  ＋ Add folder  to choose a folder of .tif files")
        self.folder_placeholder = tk.Label(self.folder_listbox, text=hint,
                                           font=("Segoe UI", 10), cursor="hand2")
        self.folder_placeholder.bind("<Button-1>", lambda e: self.add_folder())

        if DND_AVAILABLE:
            # Dropping anywhere on the card works, not just exactly on the list.
            for widget in (self.folder_listbox, self.folder_placeholder, card, list_frame):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)

        buttons = ttk.Frame(card)
        buttons.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(self.px(10), 0))
        self.remove_button = ttk.Button(buttons, text="Remove selected", command=self.remove_selected)
        self.remove_button.pack(side="left")
        self.clear_button = ttk.Button(buttons, text="Clear all", command=self.clear_folders)
        self.clear_button.pack(side="left", padx=(self.px(8), 0))
        ttk.Label(buttons, text="Settings on the left apply to every folder",
                  style="Caption.TLabel").pack(side="right")
        return card

    def _build_run_row(self, parent):
        row = ttk.Frame(parent)
        row.columnconfigure(1, weight=1)
        self.run_button = ttk.Button(row, text="▶  Run all folders", style="Run.Accent.TButton",
                                     command=self.start_run)
        self.run_button.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, self.px(16)))
        self.status_label = ttk.Label(row, textvariable=self.status_text, style="Status.TLabel")
        self.status_label.grid(row=0, column=1, sticky="w")
        self._status_kind = "idle"
        # Empty while idle, sweeping while running, full when finished
        self.progress = ttk.Progressbar(row, mode="determinate", value=0)
        self.progress.grid(row=1, column=1, sticky="ew", pady=(self.px(4), 0))
        return row

    def _build_log_card(self, parent):
        card, header = self._card(parent, "Log")
        ttk.Button(header, text="Clear", command=self._clear_log).pack(side="right")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        self.log_text = tk.Text(
            card, wrap="word", width=60, height=11, state="disabled", relief="flat", bd=0,
            highlightthickness=1, font=self._mono_font, padx=self.px(10), pady=self.px(8))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(self.px(4), 0))
        self.log_text.config(yscrollcommand=scrollbar.set)
        return card

    # ── state updates ──────────────────────────────────────────────────────

    def pick_color(self, color_var, swatch, label):
        result = colorchooser.askcolor(color=color_var.get(), title=f"Choose color for {label}")
        if result and result[1]:
            color_var.set(result[1])
            self._draw_swatch(swatch, result[1])

    def _update_ch1_mode_state(self):
        manual_widgets = [self.ch1_min_entry, self.ch1_max_entry] + self.ch1_headings
        if self.ch1_auto_var.get():
            for widget in manual_widgets:
                widget.grid_remove()
            self.ch1_brightness_frame.grid()
        else:
            self.ch1_brightness_frame.grid_remove()
            for widget in manual_widgets:
                widget.grid()

    def _update_count_state(self):
        enabled = self.count_enabled.get()
        state = ["!disabled"] if enabled else ["disabled"]
        for widget in (self.count_blur_entry, self.count_expansion_entry, self.count_min_entry,
                       self.count_max_entry, self.count_watershed_check, self.count_edges_check,
                       self.count_manual_entry, self.count_manual_label):
            widget.state(state)
        self.count_method_combo.config(state="readonly" if enabled else "disabled")
        if self.count_method.get() == "Manual":
            self.count_manual_label.grid()
            self.count_manual_entry.grid()
        else:
            self.count_manual_label.grid_remove()
            self.count_manual_entry.grid_remove()

    def _update_folder_count(self):
        n = len(self.source_folders)
        self.folder_count_text.set(f"{n} folder{'s' if n != 1 else ''}" if n else "")
        if n:
            self.folder_placeholder.place_forget()
        else:
            self.folder_placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _set_status(self, text, kind="idle"):
        self._status_kind = kind
        self.status_text.set(text)
        self.status_label.config(style={"ok": "StatusOk.TLabel", "error": "StatusError.TLabel"}
                                 .get(kind, "Status.TLabel"))

    def _read_count_settings(self):
        """Parse the cell-counting fields. Returns None if counting is off,
        or raises ValueError with a user-facing message."""
        if not self.count_enabled.get():
            return None
        try:
            settings = {
                "threshold_method": self.count_method.get(),
                "manual_threshold": float(self.count_manual_threshold.get()),
                "blur_sigma": float(self.count_blur.get()),
                "min_area_um2": float(self.count_min_area.get()),
                "max_area_um2": float(self.count_max_area.get()) if self.count_max_area.get().strip() else None,
                "watershed": self.count_watershed.get(),
                "exclude_edges": self.count_exclude_edges.get(),
                "expansion_um": float(self.count_expansion.get()),
            }
        except ValueError:
            raise ValueError("Cell counting fields must be numbers (nucleus max size may be left blank).")
        if settings["blur_sigma"] < 0 or settings["min_area_um2"] < 0 or settings["expansion_um"] < 0:
            raise ValueError("Blur, minimum size and cell expansion can't be negative.")
        if settings["max_area_um2"] is not None and settings["max_area_um2"] <= settings["min_area_um2"]:
            raise ValueError("Nucleus max size must be greater than the min size.")
        return settings

    # ── folders ────────────────────────────────────────────────────────────

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select a source folder containing TIFF files")
        if folder and folder not in self.source_folders:
            self.source_folders.append(folder)
            self.folder_listbox.insert("end", folder)
        elif folder in self.source_folders:
            messagebox.showinfo("Already added", "That folder is already in the list.")
        self._update_folder_count()

    def on_drop(self, event):
        # event.data is a Tcl list of dropped paths (braces around any path
        # containing spaces) -- self.tk.splitlist parses that correctly.
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]

        added, skipped_files, skipped_dupes = [], [], []
        for path in paths:
            path = path.strip("{}")
            if os.path.isdir(path):
                if path not in self.source_folders:
                    self.source_folders.append(path)
                    self.folder_listbox.insert("end", path)
                    added.append(path)
                else:
                    skipped_dupes.append(path)
            elif os.path.isfile(path):
                skipped_files.append(path)
        self._update_folder_count()

        if skipped_files and not added:
            messagebox.showinfo(
                "Folders only",
                "Please drop folders, not individual files. "
                "Drag the folder that contains your .tif files instead."
            )
        elif skipped_dupes and not added:
            messagebox.showinfo("Already added", "That folder is already in the list.")

    def remove_selected(self):
        selected = list(self.folder_listbox.curselection())
        for index in reversed(selected):
            self.folder_listbox.delete(index)
            del self.source_folders[index]
        self._update_folder_count()

    def clear_folders(self):
        self.folder_listbox.delete(0, "end")
        self.source_folders.clear()
        self._update_folder_count()

    # ── log ────────────────────────────────────────────────────────────────

    @staticmethod
    def _log_tag(message):
        if "ERROR" in message or "FAILED" in message:
            return "error"
        if "Batch complete" in message:
            return "ok" if " 0 failed" in message else "warn"
        if "Counted" in message or "OK:" in message or "All done" in message:
            return "ok"
        if "Step " in message and ("complete" not in message):
            return "step"
        if "No " in message or "Single-channel" in message or "Renamed" in message:
            return "dim"
        return None

    def log(self, message):
        tag = self._log_tag(message)

        def append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", message + "\n", tag or ())
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, append)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ── running ────────────────────────────────────────────────────────────

    def start_run(self):
        folders = list(self.source_folders)
        if not folders:
            messagebox.showerror("No folders selected", "Please add at least one source folder first.")
            return
        missing = [f for f in folders if not os.path.isdir(f)]
        if missing:
            messagebox.showerror("Folder not found", "One or more selected folders no longer exist:\n" + "\n".join(missing))
            return

        try:
            pixels_per_um = float(self.pixels_per_um.get())
            ch1_min = float(self.ch1_min.get())
            ch1_max = float(self.ch1_max.get())
            ch2_min = float(self.ch2_min.get())
            ch2_max = float(self.ch2_max.get())
            ch3_min = float(self.ch3_min.get())
            ch3_max = float(self.ch3_max.get())
            ch1_brightness = float(self.ch1_brightness.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Scale and threshold fields must be numbers.")
            return

        ch1_auto = self.ch1_auto_var.get()
        ch1_color = self.ch1_color.get()
        ch2_color = self.ch2_color.get()
        ch3_color = self.ch3_color.get()

        if pixels_per_um <= 0:
            messagebox.showerror("Invalid value", "Pixels per micron must be greater than 0.")
            return
        if (not ch1_auto and ch1_max <= ch1_min) or ch2_max <= ch2_min or ch3_max <= ch3_min:
            messagebox.showerror("Invalid range", "Each channel's max must be greater than its min.")
            return

        try:
            count_settings = self._read_count_settings()
        except ValueError as e:
            messagebox.showerror("Invalid cell counting settings", str(e))
            return

        for button in (self.run_button, self.add_button, self.remove_button, self.clear_button):
            button.state(["disabled"])
        self.progress.config(mode="indeterminate", value=0)
        self.progress.start(10)
        self._clear_log()
        self._set_status(f"Processing {len(folders)} folder{'s' if len(folders) != 1 else ''}…")

        thread = threading.Thread(
            target=self._run_all_worker,
            args=(folders, pixels_per_um, ch1_auto, ch1_min, ch1_max,
                  ch2_min, ch2_max, ch3_min, ch3_max, ch1_brightness,
                  ch1_color, ch2_color, ch3_color, count_settings),
            daemon=True
        )
        thread.start()

    def _run_all_worker(self, folders, pixels_per_um, ch1_auto, ch1_min, ch1_max,
                         ch2_min, ch2_max, ch3_min, ch3_max, ch1_brightness,
                         ch1_color, ch2_color, ch3_color, count_settings):
        results = {}  # folder -> (True, output_path) or (False, error_message)

        def process_one(folder):
            tag = os.path.basename(folder.rstrip("/\\")) or folder

            def tagged_log(message):
                self.log(f"[{tag}] {message}")

            try:
                output = run_pipeline(
                    folder, pixels_per_um, ch1_auto, ch1_min, ch1_max,
                    ch2_min, ch2_max, ch3_min, ch3_max,
                    ch1_brightness, ch1_color, ch2_color, ch3_color,
                    tagged_log, open_output=False, count_settings=count_settings
                )
                return folder, True, output
            except Exception as e:
                tagged_log(f"ERROR: {e}")
                return folder, False, str(e)

        max_workers = min(len(folders), os.cpu_count() or 4, 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_one, folder) for folder in folders]
            for future in concurrent.futures.as_completed(futures):
                folder, success, detail = future.result()
                results[folder] = (success, detail)

        succeeded = [(f, d) for f, (ok, d) in results.items() if ok]
        failed = [(f, d) for f, (ok, d) in results.items() if not ok]

        summary_lines = [f"\n{'='*50}", f"Batch complete: {len(succeeded)} succeeded, {len(failed)} failed.\n"]
        for folder, output in succeeded:
            summary_lines.append(f"  OK: {folder}\n    -> {output}")
        for folder, error in failed:
            summary_lines.append(f"  FAILED: {folder}\n    -> {error}")
        self.log("\n".join(summary_lines))

        def show_summary():
            msg = f"{len(succeeded)} of {len(folders)} folder(s) completed successfully."
            if failed:
                self._set_status(f"Finished with errors — {len(failed)} of {len(folders)} failed", "error")
                msg += f"\n\n{len(failed)} folder(s) failed — see the log for details."
                messagebox.showwarning("Batch finished with errors", msg)
            else:
                self._set_status(f"Done — {len(succeeded)} of {len(folders)} folders processed", "ok")
                messagebox.showinfo("Batch complete", msg)
            # Open each successful output folder once, now that everything's done
            for _, output in succeeded:
                try:
                    os.startfile(output)
                except Exception:
                    pass

        self.after(0, self._finish)
        self.after(0, show_summary)

    def _finish(self):
        self.progress.stop()
        self.progress.config(mode="determinate", value=100)
        for button in (self.run_button, self.add_button, self.remove_button, self.clear_button):
            button.state(["!disabled"])


if __name__ == "__main__":
    enable_dpi_awareness()
    app = App()
    app.mainloop()
