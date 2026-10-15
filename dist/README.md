# Fluorescence Channel Merger
### v4.0

A simple desktop app for the Gaborski Lab that takes raw single-channel TIFF
images from the microscope (DAPI, GFP, TxRed — any combination) and automatically:

1. Renames files so channels are consistently labeled
2. Merges matching channels into a single composite TIFF (1, 2, or 3 channels — whatever's present)
3. Applies per-channel intensity thresholds
4. Adds a scale bar
5. Exports a false-color RGB PNG for each image
6. Logs all the settings used (per image) to a CSV file
7. Counts cells from the DAPI channel (Fiji-style) and measures each
   channel's intensity per image and per cell

It can also process **multiple source folders at once**, running them
concurrently rather than one at a time, and folders can be added by
dragging and dropping them onto the window.

No coding or command line needed — just run the app, add your folder(s), set
your thresholds, and click **Run**.

---

## What's new in v4.0

- **A modern look**: a Windows 11-style interface with **light and dark
  modes**. It starts in whichever mode Windows is set to, and the **Dark mode**
  switch in the top-right corner flips it at any time
- **Sharp text on high-resolution laptops and monitors** (previously Windows
  stretched the whole window on scaled displays, which made it blurry)
- A two-column layout: settings on the left, folders, the Run button and the
  log on the right
- **Hover help**: hover over any ⓘ icon to see what that setting does
- A **color-coded log** (errors in red, cell counts in green, step headers in
  bold) and a status line under the Run button
- Only the fields you need are shown, e.g. DAPI shows either its brightness
  factor or its min/max, depending on the auto-stretch switch
- The image processing and all output files are exactly the same as v3.2

## What's new in v3.2

- **Batch cell counting from DAPI**, using the same recipe you'd use in Fiji
  (blur → auto-threshold → fill holes → watershed → Analyze Particles with a
  size filter), run automatically on every image in every folder
- **Average intensity of each channel** per image (whole image, inside cells,
  and background)
- **Per-cell intensities** for every channel (mean, integrated density, and
  background-subtracted mean), one row per cell
- An **outline overlay** PNG per image so you can check what was counted
- The window now sizes itself to fit, so nothing gets cut off on
  high-resolution / scaled displays

## What's new in v3.1

- Each channel's display color is now user-selectable via a color picker, instead of being fixed to DAPI=blue, GFP=green, TxRed=red
- `metadata.csv` now records the exact color used for each channel per image (`Ch1_Color`, `Ch2_Color`, `Ch3_Color` columns)

## What's new in v3.0

- Manual min/max thresholding for the DAPI (Ch1) channel, same as GFP and TxRed — toggle between this and the original auto-stretch-per-image behavior with a single checkbox
- `metadata.csv` now records whether each image's DAPI channel used auto or manual mode (`Ch1_Mode` column)

## What's new in v2.0

- Support for any combination of channels per image (previously required DAPI + GFP at minimum)
- Batch processing of multiple source folders in a single run, processed concurrently
- Drag-and-drop folders directly onto the app window
- Per-image channel tracking in `metadata.csv` (blank columns for channels not present in a given image, instead of misleading values)

---

## What you need

- **Windows PC** (the packaged `.exe` runs standalone — no Python required)
- Your raw `.tif` files, one file per channel per image

**File naming:** your TIFF filenames need to contain (case-insensitive)
whichever of these channels apply to that image:
- `DAPI` for the nuclear channel
- `GFP` for the green fluorescent protein channel
- `TxRed` for the Texas Red channel

Any single channel, any pair, or all three is fine — the app figures out
what's present per image. You just need at least one of the three. Each
channel's display color is customizable in the app (see below) — DAPI/GFP/TxRed
are just the channel names/labels, not fixed colors.

The app renames these automatically to `ch1`, `ch2`, `ch3` internally — you
don't need to rename anything yourself, just make sure the original filenames
contain those channel names and that matching images share the same base
filename otherwise (e.g. `Image01_DAPI.tif`, `Image01_GFP.tif`,
`Image01_TxRed.tif`).

**Mixed batches are fine.** A single folder can contain a mix of 1-channel,
2-channel, and 3-channel image sets — the app checks each image individually
and merges/thresholds/colors exactly the channels that image actually has.
Missing channels are simply left blank (black) in the final PNG rather than
causing an error.

---

## Getting started

1. Download `FluorescenceChannelMerger.exe`.
2. Double-click it to launch. No installation needed.
3. Windows may show a **SmartScreen warning** the first time, since the app
   isn't signed with a paid certificate. Click **More info → Run anyway**.
   This is normal for in-house lab tools and not a sign of a problem.

---

## Using the app

### 1. Source Folders
Click **＋ Add folder** (or drag folders onto the Source folders panel) and select a folder containing your raw `.tif` files.
You can add as many folders as you want — each one appears in the list, and
they'll all be processed together using the same settings below.

- **Remove selected** — removes whichever folder(s) you've highlighted in the list
- **Clear all** — empties the list entirely

For each folder, the app creates two new folders next to it:
- `<foldername> - composite` — merged composite TIFFs (1–3 channels, for re-analysis e.g. in QuPath/Fiji)
- `<foldername> - PNG` — final false-color PNGs + `metadata.csv`

**Note:** the settings on the left of the window apply to *every* folder in
the list — there's currently no way to set different thresholds per folder
in a single run. If different folders need different threshold values,
run them as separate batches.

### 2. Microscope Scale
Enter **pixels per micron** for your objective (this determines the scale
bar size). Some common values:
- Leica 10x: `0.885`
- Check your microscope's calibration if you're using a different objective/zoom.

### 3. Channel Thresholds & Colors
Each channel has a **color square** next to its name — click it to open a
color picker and choose any color you want for that
channel. Defaults are the traditional **DAPI → blue, GFP → green, TxRed →
red**, but you're free to change any or all of them (useful for
colorblind-friendly palettes, matching a figure style, or just personal
preference). Colors blend additively where channels overlap, the same way
real fluorescence channels do.

- **DAPI (Ch1)** has two threshold modes, toggled with the **"Auto-stretch each image"**
  switch:
  - **Auto-stretch (default)** — DAPI is automatically scaled to its own
    min/max in each image, then multiplied by the **Brightness ×**
    field to boost visibility. Higher = brighter nuclei. Default: `2`.
    This adapts per image, so it's a good hands-off default across a varied batch.
  - **Manual** — switch it off to enter your own **min / max** values
    instead, applied the same way as GFP/TxRed below. Useful when you want
    identical DAPI scaling across every image in a batch rather than each
    image auto-adjusting to its own brightness.
- **GFP (Ch2) Min / Max** — raw intensity values that get mapped to
  full color intensity for that channel. Anything below min shows as black;
  anything above max is fully saturated.
- **TxRed (Ch3) Min / Max** — same idea, for the TxRed channel. If an image
  doesn't have a TxRed file, these settings are simply ignored for that image
  and that channel's color stays out of the blend. The same goes for Ch1/Ch2
  settings if an image is missing DAPI or GFP — you don't need to change
  anything based on which channels a given folder happens to have.

**Tip:** If everything looks too dim or too saturated, open one raw image in
Fiji/ImageJ, check the actual pixel intensity range for that channel, and use
those numbers as your min/max here. These settings apply to your whole batch,
so use values that make sense across all the images you're processing together.

### 4. Cell Counting & Intensities
With the switch in the top-right of the **Cell counting** panel turned on
(the default), every image that has a DAPI channel gets its nuclei
segmented and counted, and every channel present gets measured. Turn it off
to skip this step and run exactly like v3.1.

All measurements use the **raw pixel values** from the microscope, not the
thresholded/colored PNG, so the display min/max settings above don't affect
the numbers.

How nuclei are found (each step mirrors what you'd do by hand in Fiji):
1. **Blur σ (px)** — Gaussian blur on DAPI to smooth out noise and
   texture (Fiji: Process › Filters › Gaussian Blur). Default `2`; set `0` to skip.
2. **Threshold** — the method used to separate nuclei from background.
   `Otsu` (default), `Li`, `Triangle`, `Default (IsoData)` and `Mean` pick a
   threshold automatically for each image (Fiji: Image › Adjust › Threshold).
   Choose `Manual` to use a fixed raw DAPI value for every image instead —
   useful if the auto methods pick up too much/too little in a dim or very
   sparse image.
3. Holes inside nuclei are filled.
4. **Split touching nuclei** — splits touching/overlapping nuclei so they count as two
   (Fiji: Process › Binary › Watershed). Turn it off if it's chopping single
   nuclei in half.
5. **Size min / Max (µm²)** — objects outside this range are thrown
   out, e.g. debris and specks below the min, or clumps above the max
   (Fiji: Analyze Particles › Size). Default min `20` µm², no max.
6. **Skip edge nuclei** — drop nuclei cut off by the image
   border (Fiji: Analyze Particles › Exclude on edges). Off by default.

**Expand (µm)** — DAPI only marks the nucleus, but GFP/TxRed signal
is often in the cytoplasm. Setting this above `0` grows each nucleus outward
by that distance (without overlapping its neighbors) and measures GFP/TxRed
in that larger area, as an approximation of the whole cell. DAPI is always
measured in the nucleus itself. Default `0` = measure everything inside the
nucleus only.

**Always open a few outline images** (see Output) the first time you use new
settings or a new cell type, to check the counting looks right before
trusting the numbers.

### 5. Run all folders
Click **▶ Run all folders**. If you've added more than one folder, they're
processed **concurrently** (at the same time), not one after another — so
batching several folders together is faster than running the app separately
for each one.

Progress and warnings print in the **Log** panel (errors in red, cell counts
in green), with each line tagged by folder
name (e.g. `[Experiment1] Processed: ...`) so you can tell which folder a
message belongs to even with several running at once.

When everything finishes, you'll get a summary popup showing how many
folders succeeded and how many failed, and every successfully processed
output folder opens automatically. If one folder hits an error, it doesn't
stop the others — you'll see exactly which folder failed and why, both in
the log and in the summary.

---

## Output

Each source folder gets its own independent set of outputs — nothing from
different folders gets mixed together, even when they're processed in the
same batch.

For every image with at least one channel present, you'll get:
- One merged composite `.tif` (1, 2, or 3 channels, depending on which were
  present) in the `- composite` folder
- One thresholded, scale-barred `.png` in the `- PNG` folder
- One row in `metadata.csv` recording the filename, which channels were
  present, image dimensions, scale, and the threshold values actually used —
  so you can always trace back exactly how a given image was processed.
  This includes a `Ch1_Mode` column showing whether DAPI used auto-stretch
  or manual thresholds, and `Ch1_Color`/`Ch2_Color`/`Ch3_Color` columns
  recording the exact color used for each channel in that image. Columns
  for any channel that wasn't present in that image are left blank rather
  than showing values that weren't actually applied.

### Cell counting output

With cell counting on, each source folder also gets a
`<foldername> - Cell Counts` folder containing:

- **`cell_count_summary.csv`** — one row per image:
  - `Cell_Count`, `Cells_per_mm2`, `Mean_Nucleus_Area_um2`,
    `Total_Nucleus_Area_um2`, `Nucleus_Area_Fraction` (fraction of the image
    covered by nuclei), plus the threshold method and the raw DAPI threshold
    value that was actually used
  - For each channel (`DAPI_`, `GFP_`, `TxRed_`):
    - `_Image_Mean` — average intensity of the whole image
    - `_Background` — median intensity of everything outside the cells
    - `_Mean_In_Cells` — average of all pixels inside cells
    - `_Mean_of_Cell_Means` — average of the per-cell means (each cell
      weighted equally, regardless of size)
- **`per_cell_measurements.csv`** — one row per cell, across all images in
  the folder (the `Image` column says which image):
  - `Cell` number (matches the number drawn on the outline image),
    centroid `X_px`/`Y_px`, `Nucleus_Area_um2`, `Circularity` (1 = perfect
    circle, like Fiji), and `Cell_Area_um2` when cell expansion is used
  - For each channel: `_Mean`, `_IntDen` (integrated density = sum of all
    pixel values in the cell, like Fiji's RawIntDen), and `_Mean_BgSub`
    (mean minus that image's background)
- **`outlines/<image>_outlines.png`** — the DAPI image in grayscale with
  every counted nucleus outlined in yellow and numbered in cyan.

Images without a DAPI channel can't be counted, but still get a summary row
with their whole-image channel averages. Columns for channels an image
doesn't have are left blank, the same as in `metadata.csv`.

These CSVs open directly in Excel, Prism, R, or Python for plotting and stats.

An image only gets skipped entirely if none of the three channels can be
found for it. Any channel that's simply absent (no DAPI, no GFP, or no
TxRed) is not treated as an error — that image is processed using whichever
channels it does have, with the missing ones left blank in the final PNG.

---

## Troubleshooting

| Problem | Likely cause |
|---|---|
| A folder shows up as "FAILED" in the batch summary | Check the log lines tagged with that folder's name for the specific error — other folders in the batch still complete normally |
| "No GFP (ch2) found for..." / "No TxRed (ch3) found for..." in log | Just informational — that image is missing one channel and gets processed with whichever channels it does have |
| "Already added" when adding a folder | That exact folder is already in the list — check for duplicates |
| Colors look washed out / too dark | Adjust the min/max threshold for that channel — check real pixel values in Fiji first |
| SmartScreen blocks the app | Click "More info" → "Run anyway" (unsigned executable, expected) |
| Antivirus quarantines the file | Some AV software is aggressive with unsigned PyInstaller apps — whitelist the file, or ask IT to allow it |
| Cell count is too high (background or debris counted) | Raise **Size min**, increase **Blur σ**, or switch to `Manual` with a value above the one shown in `DAPI_Threshold` (`Li` and `Triangle` are usually more inclusive than `Otsu`, so avoid those here) |
| Cell count is too low (dim nuclei missed) | Try `Li` or `Triangle`, or a `Manual` threshold below the value shown in `DAPI_Threshold` |
| Single nuclei split in two on the outline image | Turn off **Split touching nuclei**, or increase **Blur σ** |
| Clumps of nuclei counted as one | Make sure **Split touching nuclei** is on; set a **Max (µm²)** size to drop large clumps you can't split |
| `Cell_Count` is blank for an image | That image has no DAPI channel, so there's nothing to count |
| Output folders don't open automatically | Only happens automatically on Windows, and only after the whole batch finishes; the files are still there, just navigate to the `- PNG` folder manually |

---

## Notes for anyone modifying this

The source code (`FluorescenceChannelMerger.py`) is included alongside the
`.exe`. It's a single-file Python script (Tkinter GUI + numpy/tifffile/Pillow
for image processing, scipy/scikit-image for cell segmentation, and sv-ttk +
darkdetect for the modern theme and dark mode). If you need to rebuild the `.exe` after making
changes, see `BUILD_INSTRUCTIONS.md` — it walks through the one-command
PyInstaller build.

---

*Built for the Gaborski NanoBio Materials Lab, RIT. Author: Owen Faust, June 2026.*