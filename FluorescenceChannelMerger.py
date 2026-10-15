"""
Fluorescence Channel Merger & Thresholder
------------------------------------------
Version: 3.2
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

Run with:  python FluorescenceChannelMerger.py
Requires:  numpy, tifffile, pillow, scipy, scikit-image
           (tkinter ships with standard Python)
Optional:  tkinterdnd2 (enables drag-and-drop folder support; the app runs
           fine without it, just using the "Add Folder..." dialog instead)
"""

import os
import re
import csv
import contextlib
import threading
import concurrent.futures
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage import feature, filters, measure, segmentation

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


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

_BaseWindow = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class App(_BaseWindow):
    def __init__(self):
        super().__init__()
        self.title("Fluorescence Channel Merger v3.2")
        # No fixed geometry: the window sizes itself to fit its contents, so
        # nothing gets cut off under Windows display scaling (125%/150%).
        self.resizable(False, True)

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

        pad = {"padx": 10, "pady": 6}

        # Folder selection (multiple folders supported)
        folder_frame_title = "Source Folders (drag & drop folders here, or use Add Folder...)" \
            if DND_AVAILABLE else "Source Folders (processed together, same settings)"
        frame_folder = ttk.LabelFrame(self, text=folder_frame_title)
        frame_folder.pack(fill="x", **pad)

        if not DND_AVAILABLE:
            ttk.Label(
                frame_folder,
                text="Tip: install the 'tkinterdnd2' package to enable drag-and-drop here.",
                foreground="#666666"
            ).pack(anchor="w", padx=8, pady=(6, 0))

        list_row = ttk.Frame(frame_folder)
        list_row.pack(fill="x", padx=8, pady=8)
        self.folder_listbox = tk.Listbox(list_row, height=5, selectmode="extended")
        self.folder_listbox.pack(side="left", fill="x", expand=True)
        scrollbar = ttk.Scrollbar(list_row, orient="vertical", command=self.folder_listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.folder_listbox.config(yscrollcommand=scrollbar.set)

        if DND_AVAILABLE:
            # Register both the listbox and the surrounding frame as drop targets
            # so dropping anywhere in that area works, not just exactly on the list.
            for widget in (self.folder_listbox, frame_folder, list_row):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)

        button_row = ttk.Frame(frame_folder)
        button_row.pack(fill="x", padx=8, pady=(0, 8))
        self.add_button = ttk.Button(button_row, text="Add Folder...", command=self.add_folder)
        self.add_button.pack(side="left", padx=(0, 6))
        self.remove_button = ttk.Button(button_row, text="Remove Selected", command=self.remove_selected)
        self.remove_button.pack(side="left", padx=(0, 6))
        self.clear_button = ttk.Button(button_row, text="Clear All", command=self.clear_folders)
        self.clear_button.pack(side="left")

        # Scale
        frame_scale = ttk.LabelFrame(self, text="Microscope Scale")
        frame_scale.pack(fill="x", **pad)
        ttk.Label(frame_scale, text="Pixels per micron:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(frame_scale, textvariable=self.pixels_per_um, width=12).grid(row=0, column=1, padx=8, pady=8)
        ttk.Label(frame_scale, text="(e.g. 0.885 for Leica 10x)").grid(row=0, column=2, sticky="w", padx=8)

        # Channel thresholds
        frame_thresh = ttk.LabelFrame(self, text="Channel Thresholds (applied to every folder above)")
        frame_thresh.pack(fill="x", **pad)

        ttk.Label(frame_thresh, text="Ch1 (DAPI):").grid(
            row=0, column=0, sticky="w", padx=8, pady=6)
        self.ch1_auto_check = ttk.Checkbutton(
            frame_thresh, text="Auto-stretch per image", variable=self.ch1_auto_var,
            command=self._update_ch1_mode_state
        )
        self.ch1_auto_check.grid(row=0, column=1, columnspan=3, sticky="w", padx=8, pady=6)
        ttk.Label(frame_thresh, text="Color:").grid(row=0, column=4, sticky="w", padx=(4, 2))
        self.ch1_color_button = tk.Button(
            frame_thresh, text="  ", width=3, bg=self.ch1_color.get(),
            command=lambda: self.pick_color(self.ch1_color, self.ch1_color_button, "DAPI (Ch1)")
        )
        self.ch1_color_button.grid(row=0, column=5, padx=(0, 8))

        ttk.Label(frame_thresh, text="  Brightness multiplier (auto-stretch only):").grid(
            row=1, column=0, sticky="w", padx=8, pady=6)
        self.ch1_brightness_entry = ttk.Entry(frame_thresh, textvariable=self.ch1_brightness, width=10)
        self.ch1_brightness_entry.grid(row=1, column=1, padx=8, pady=6)

        ttk.Label(frame_thresh, text="  Manual min:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.ch1_min_entry = ttk.Entry(frame_thresh, textvariable=self.ch1_min, width=10)
        self.ch1_min_entry.grid(row=2, column=1, padx=8, pady=6)
        ttk.Label(frame_thresh, text="max:").grid(row=2, column=2, sticky="w")
        self.ch1_max_entry = ttk.Entry(frame_thresh, textvariable=self.ch1_max, width=10)
        self.ch1_max_entry.grid(row=2, column=3, padx=8, pady=6)

        ttk.Separator(frame_thresh, orient="horizontal").grid(
            row=3, column=0, columnspan=6, sticky="ew", padx=8, pady=6)

        ttk.Label(frame_thresh, text="Ch2 (GFP) min:").grid(row=4, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(frame_thresh, textvariable=self.ch2_min, width=10).grid(row=4, column=1, padx=8, pady=6)
        ttk.Label(frame_thresh, text="max:").grid(row=4, column=2, sticky="w")
        ttk.Entry(frame_thresh, textvariable=self.ch2_max, width=10).grid(row=4, column=3, padx=8, pady=6)
        ttk.Label(frame_thresh, text="Color:").grid(row=4, column=4, sticky="w", padx=(4, 2))
        self.ch2_color_button = tk.Button(
            frame_thresh, text="  ", width=3, bg=self.ch2_color.get(),
            command=lambda: self.pick_color(self.ch2_color, self.ch2_color_button, "GFP (Ch2)")
        )
        self.ch2_color_button.grid(row=4, column=5, padx=(0, 8))

        ttk.Label(frame_thresh, text="Ch3 (TxRed) min:").grid(row=5, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(frame_thresh, textvariable=self.ch3_min, width=10).grid(row=5, column=1, padx=8, pady=6)
        ttk.Label(frame_thresh, text="max:").grid(row=5, column=2, sticky="w")
        ttk.Entry(frame_thresh, textvariable=self.ch3_max, width=10).grid(row=5, column=3, padx=8, pady=6)
        ttk.Label(frame_thresh, text="Color:").grid(row=5, column=4, sticky="w", padx=(4, 2))
        self.ch3_color_button = tk.Button(
            frame_thresh, text="  ", width=3, bg=self.ch3_color.get(),
            command=lambda: self.pick_color(self.ch3_color, self.ch3_color_button, "TxRed (Ch3)")
        )
        self.ch3_color_button.grid(row=5, column=5, padx=(0, 8))

        self._update_ch1_mode_state()

        # Cell counting
        frame_count = ttk.LabelFrame(self, text="Cell Counting & Intensities (nuclei from DAPI)")
        frame_count.pack(fill="x", **pad)

        ttk.Checkbutton(
            frame_count, text="Count cells and measure per-cell / per-image intensities",
            variable=self.count_enabled, command=self._update_count_state
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=6)

        ttk.Label(frame_count, text="Threshold:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.count_method_combo = ttk.Combobox(
            frame_count, textvariable=self.count_method, state="readonly", width=16,
            values=list(THRESHOLD_METHODS) + ["Manual"]
        )
        self.count_method_combo.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self.count_method_combo.bind("<<ComboboxSelected>>", lambda e: self._update_count_state())
        ttk.Label(frame_count, text="Manual value:").grid(row=1, column=2, sticky="w", padx=8)
        self.count_manual_entry = ttk.Entry(frame_count, textvariable=self.count_manual_threshold, width=10)
        self.count_manual_entry.grid(row=1, column=3, sticky="w", padx=8, pady=4)

        ttk.Label(frame_count, text="Blur sigma (px):").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.count_blur_entry = ttk.Entry(frame_count, textvariable=self.count_blur, width=10)
        self.count_blur_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(frame_count, text="Cell expansion (um):").grid(row=2, column=2, sticky="w", padx=8)
        self.count_expansion_entry = ttk.Entry(frame_count, textvariable=self.count_expansion, width=10)
        self.count_expansion_entry.grid(row=2, column=3, sticky="w", padx=8, pady=4)

        ttk.Label(frame_count, text="Nucleus size min (um²):").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.count_min_entry = ttk.Entry(frame_count, textvariable=self.count_min_area, width=10)
        self.count_min_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(frame_count, text="max (blank = none):").grid(row=3, column=2, sticky="w", padx=8)
        self.count_max_entry = ttk.Entry(frame_count, textvariable=self.count_max_area, width=10)
        self.count_max_entry.grid(row=3, column=3, sticky="w", padx=8, pady=4)

        self.count_watershed_check = ttk.Checkbutton(
            frame_count, text="Watershed (split touching nuclei)", variable=self.count_watershed)
        self.count_watershed_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 8))
        self.count_edges_check = ttk.Checkbutton(
            frame_count, text="Exclude nuclei on image edges", variable=self.count_exclude_edges)
        self.count_edges_check.grid(row=4, column=2, columnspan=2, sticky="w", padx=8, pady=(4, 8))

        self._update_count_state()

        # Run button + progress
        frame_run = ttk.Frame(self)
        frame_run.pack(fill="x", **pad)
        self.run_button = ttk.Button(frame_run, text="Run All Folders", command=self.start_run)
        self.run_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(frame_run, mode="indeterminate", length=280)
        self.progress.pack(side="left", padx=8)

        # Log output
        frame_log = ttk.LabelFrame(self, text="Log")
        frame_log.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(frame_log, wrap="word", height=12, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def pick_color(self, color_var, button, label):
        current = color_var.get()
        result = colorchooser.askcolor(color=current, title=f"Choose color for {label}")
        if result and result[1]:
            hex_color = result[1]
            color_var.set(hex_color)
            button.config(bg=hex_color, activebackground=hex_color)

    def _update_ch1_mode_state(self):
        if self.ch1_auto_var.get():
            self.ch1_brightness_entry.config(state="normal")
            self.ch1_min_entry.config(state="disabled")
            self.ch1_max_entry.config(state="disabled")
        else:
            self.ch1_brightness_entry.config(state="disabled")
            self.ch1_min_entry.config(state="normal")
            self.ch1_max_entry.config(state="normal")

    def _update_count_state(self):
        enabled = self.count_enabled.get()
        state = "normal" if enabled else "disabled"
        for widget in (self.count_blur_entry, self.count_expansion_entry, self.count_min_entry,
                       self.count_max_entry, self.count_watershed_check, self.count_edges_check):
            widget.config(state=state)
        self.count_method_combo.config(state="readonly" if enabled else "disabled")
        manual = enabled and self.count_method.get() == "Manual"
        self.count_manual_entry.config(state="normal" if manual else "disabled")

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

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select a source folder containing TIFF files")
        if folder and folder not in self.source_folders:
            self.source_folders.append(folder)
            self.folder_listbox.insert("end", folder)
        elif folder in self.source_folders:
            messagebox.showinfo("Already added", "That folder is already in the list.")

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

    def clear_folders(self):
        self.folder_listbox.delete(0, "end")
        self.source_folders.clear()

    def log(self, message):
        def append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, append)

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

        self.run_button.config(state="disabled")
        self.add_button.config(state="disabled")
        self.remove_button.config(state="disabled")
        self.clear_button.config(state="disabled")
        self.progress.start(10)
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

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
                msg += f"\n\n{len(failed)} folder(s) failed — see the log for details."
                messagebox.showwarning("Batch finished with errors", msg)
            else:
                messagebox.showinfo("Batch complete", msg)
            # Open each successful output folder once, now that everything's done
            for _, output in succeeded:
                try:
                    os.startfile(output)
                except Exception:
                    pass

        self.after(0, show_summary)
        self.after(0, self._finish)

    def _finish(self):
        self.progress.stop()
        self.run_button.config(state="normal")
        self.add_button.config(state="normal")
        self.remove_button.config(state="normal")
        self.clear_button.config(state="normal")


if __name__ == "__main__":
    app = App()
    app.mainloop()