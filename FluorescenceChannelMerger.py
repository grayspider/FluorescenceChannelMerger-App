"""
Fluorescence Channel Merger & Thresholder
------------------------------------------
Version: 3.1
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

Run with:  python FluorescenceChannelMerger.py
Requires:  numpy, tifffile, pillow   (tkinter ships with standard Python)
Optional:  tkinterdnd2 (enables drag-and-drop folder support; the app runs
           fine without it, just using the "Add Folder..." dialog instead)
"""

import os
import re
import csv
import threading
import concurrent.futures
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

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


def run_pipeline(source, pixels_per_um, ch1_auto, ch1_min_setting, ch1_max_setting,
                  ch2_min, ch2_max, ch3_min, ch3_max,
                  ch1_brightness, ch1_color, ch2_color, ch3_color,
                  log, open_output=True):
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

    os.makedirs(composite, exist_ok=True)
    os.makedirs(output, exist_ok=True)

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

    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "Filename", "Channels", "Width", "Height", "PixelsPerUm",
            "Ch1_Mode", "Ch1_Min", "Ch1_Max", "Ch1_Color",
            "Ch2_Min", "Ch2_Max", "Ch2_Color",
            "Ch3_Min", "Ch3_Max", "Ch3_Color"
        ])

        for filename in os.listdir(composite):
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

    log("Step 3 complete.")
    log(f"\nAll done. Metadata saved to:\n{csv_path}")

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
        self.title("Fluorescence Channel Merger v3.1")
        self.geometry("560x720")
        self.resizable(False, False)

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
        self.log_text = tk.Text(frame_log, wrap="word", height=18, state="disabled")
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
                  ch1_color, ch2_color, ch3_color),
            daemon=True
        )
        thread.start()

    def _run_all_worker(self, folders, pixels_per_um, ch1_auto, ch1_min, ch1_max,
                         ch2_min, ch2_max, ch3_min, ch3_max, ch1_brightness,
                         ch1_color, ch2_color, ch3_color):
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
                    tagged_log, open_output=False
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