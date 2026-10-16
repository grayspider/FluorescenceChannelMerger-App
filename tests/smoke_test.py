"""Quick end-to-end check of the image pipeline on synthetic images.

Makes a temporary folder of fake DAPI/GFP/TxRed TIFFs with a known number of
nuclei (including touching pairs), runs the full pipeline with cell counting,
and checks the outputs. No real data or GUI needed.

Run with:  python tests/smoke_test.py
"""
import csv
import os
import sys
import tempfile

import numpy as np
import tifffile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import FluorescenceChannelMerger as fcm  # noqa: E402

SIZE = 512
RADIUS = 6
PIXELS_PER_UM = 0.885


def make_image(rng, n_single, n_pairs):
    yy, xx = np.mgrid[:SIZE, :SIZE]
    dapi = np.full((SIZE, SIZE), 100.0)
    gfp = np.full((SIZE, SIZE), 50.0)
    occupied = np.zeros((SIZE, SIZE), bool)

    def place(cy, cx, gfp_level):
        disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= RADIUS ** 2
        dapi[disk] = 1000
        gfp[(yy - cy) ** 2 + (xx - cx) ** 2 <= (RADIUS + 3) ** 2] = gfp_level
        occupied[max(cy - 3 * RADIUS, 0):cy + 3 * RADIUS, max(cx - 3 * RADIUS, 0):cx + 3 * RADIUS] = True

    def free(cy, cx, r):
        return not occupied[max(cy - r, 0):cy + r, max(cx - r, 0):cx + r].any()

    margin = 4 * RADIUS
    placed = 0
    while placed < n_single:
        cy, cx = rng.integers(margin, SIZE - margin, 2)
        if free(cy, cx, 3 * RADIUS):
            place(cy, cx, 600)
            placed += 1
    placed = 0
    while placed < n_pairs:
        cy, cx = rng.integers(margin, SIZE - margin - 2 * RADIUS, 2)
        if free(cy, cx + RADIUS, 4 * RADIUS):
            place(cy, cx, 600)
            place(cy, cx + int(1.7 * RADIUS), 600)  # touching neighbour
            placed += 1
    dapi += rng.normal(0, 20, dapi.shape)
    return np.clip(dapi, 0, 65535).astype(np.uint16), gfp.astype(np.uint16)


def main():
    rng = np.random.default_rng(0)
    settings = {"threshold_method": "Otsu", "manual_threshold": 500, "blur_sigma": 2,
                "min_area_um2": 20, "max_area_um2": None, "watershed": True,
                "exclude_edges": False, "expansion_um": 0}
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "Synthetic")
        os.makedirs(source)
        dapi, gfp = make_image(rng, n_single=40, n_pairs=5)
        tifffile.imwrite(os.path.join(source, "Img01_DAPI.tif"), dapi)
        tifffile.imwrite(os.path.join(source, "Img01_GFP.tif"), gfp)

        fcm.run_pipeline(source, PIXELS_PER_UM, True, 0, 500, 111, 1108, 100, 1000,
                         2, "#0000ff", "#00ff00", "#ff0000", print,
                         open_output=False, count_settings=settings)

        png = os.path.join(tmp, "Synthetic - PNG", "Img01_.png")
        counts_dir = os.path.join(tmp, "Synthetic - Cell Counts")
        with open(os.path.join(counts_dir, "cell_count_summary.csv")) as f:
            summary = list(csv.DictReader(f))
        with open(os.path.join(counts_dir, "per_cell_measurements.csv")) as f:
            cells = list(csv.DictReader(f))

        expected = 40 + 2 * 5
        count = int(summary[0]["Cell_Count"])
        gfp_mean = np.median([float(c["GFP_Mean"]) for c in cells])
        checks = {
            "PNG written": os.path.exists(png),
            "outline image written": os.path.exists(os.path.join(counts_dir, "outlines", "Img01__outlines.png")),
            f"cell count {count} == {expected}": count == expected,
            f"one per-cell row per cell ({len(cells)})": len(cells) == count,
            f"GFP per-cell mean ~600 ({gfp_mean:.0f})": abs(gfp_mean - 600) < 30,
        }
    print()
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    if not all(checks.values()):
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
