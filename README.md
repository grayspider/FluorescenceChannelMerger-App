# Fluorescence Channel Merger
### v3.0

A simple desktop app for the Gaborski Lab that takes raw single-channel TIFF
images from the microscope (DAPI, GFP, TxRed — any combination) and automatically:

1. Renames files so channels are consistently labeled
2. Merges matching channels into a single composite TIFF (1, 2, or 3 channels — whatever's present)
3. Applies per-channel intensity thresholds
4. Adds a scale bar
5. Exports a false-color RGB PNG for each image
6. Logs all the settings used (per image) to a CSV file

It can also process **multiple source folders at once**, running them
concurrently rather than one at a time, and folders can be added by
dragging and dropping them onto the window.

No coding or command line needed — just run the app, add your folder(s), set
your thresholds, and click **Run**.

---

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
Click **Add Folder...** and select a folder containing your raw `.tif` files.
You can add as many folders as you want — each one appears in the list, and
they'll all be processed together using the same settings below.

- **Remove Selected** — removes whichever folder(s) you've highlighted in the list
- **Clear All** — empties the list entirely

For each folder, the app creates two new folders next to it:
- `<foldername> - composite` — merged composite TIFFs (1–3 channels, for re-analysis e.g. in QuPath/Fiji)
- `<foldername> - PNG` — final false-color PNGs + `metadata.csv`

**Note:** the threshold and scale settings below apply to *every* folder in
the list — there's currently no way to set different thresholds per folder
in a single run. If different folders need different threshold values,
run them as separate batches.

### 2. Microscope Scale
Enter **pixels per micron** for your objective (this determines the scale
bar size). Some common values:
- Leica 10x: `0.885`
- Check your microscope's calibration if you're using a different objective/zoom.

### 3. Channel Thresholds & Colors
Each channel has a **Color** swatch button next to its threshold fields —
click it to open a color picker and choose any color you want for that
channel. Defaults are the traditional **DAPI → blue, GFP → green, TxRed →
red**, but you're free to change any or all of them (useful for
colorblind-friendly palettes, matching a figure style, or just personal
preference). Colors blend additively where channels overlap, the same way
real fluorescence channels do.

- **Ch1 (DAPI)** has two threshold modes, toggled with the **"Auto-stretch per image"**
  checkbox:
  - **Auto-stretch (default)** — DAPI is automatically scaled to its own
    min/max in each image, then multiplied by the **brightness multiplier**
    field to boost visibility. Higher = brighter nuclei. Default: `2`.
    This adapts per image, so it's a good hands-off default across a varied batch.
  - **Manual** — uncheck the box to enter your own **min / max** values
    instead, applied the same way as GFP/TxRed below. Useful when you want
    identical DAPI scaling across every image in a batch rather than each
    image auto-adjusting to its own brightness.
- **Ch2 (GFP) min / max** — raw intensity values that get mapped to
  full color intensity for that channel. Anything below min shows as black;
  anything above max is fully saturated.
- **Ch3 (TxRed) min / max** — same idea, for the TxRed channel. If an image
  doesn't have a TxRed file, these settings are simply ignored for that image
  and that channel's color stays out of the blend. The same goes for Ch1/Ch2
  settings if an image is missing DAPI or GFP — you don't need to change
  anything based on which channels a given folder happens to have.

**Tip:** If everything looks too dim or too saturated, open one raw image in
Fiji/ImageJ, check the actual pixel intensity range for that channel, and use
those numbers as your min/max here. These settings apply to your whole batch,
so use values that make sense across all the images you're processing together.

### 4. Run All Folders
Click **Run All Folders**. If you've added more than one folder, they're
processed **concurrently** (at the same time), not one after another — so
batching several folders together is faster than running the app separately
for each one.

Progress and warnings print in the log box, with each line tagged by folder
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
| Output folders don't open automatically | Only happens automatically on Windows, and only after the whole batch finishes; the files are still there, just navigate to the `- PNG` folder manually |

---

## Notes for anyone modifying this

The source code (`FluorescenceChannelMerger.py`) is included alongside the
`.exe`. It's a single-file Python script (Tkinter GUI + numpy/tifffile/Pillow
for image processing). If you need to rebuild the `.exe` after making
changes, see `BUILD_INSTRUCTIONS.md` — it walks through the one-command
PyInstaller build.

---

*Built for the Gaborski NanoBio Materials Lab, RIT. Author: Owen Faust, June 2026.*
