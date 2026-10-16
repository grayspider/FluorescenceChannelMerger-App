# Building the macOS App

There are two ways to get the Mac version. **Option A needs no Mac at all.**

---

## Option A: Let GitHub build it (no Mac needed)

The repository includes a GitHub Actions workflow
(`.github/workflows/build-macos.yml`) that builds the app on GitHub's own Mac
machines, for both **Apple Silicon** (M1 and newer) and **Intel** Macs.

- **For a release:** publishing a new GitHub Release automatically builds
  the app and attaches the zips (e.g.
  `FluorescenceChannelMerger-v4.0-macOS-arm64.zip`) to that release.
- **On demand:** go to the repo's **Actions** tab → **Build macOS app** →
  **Run workflow**. Type a release tag (e.g. `v4.0`) to attach the zips to
  that release, or leave it blank and download them from the finished run's
  **Artifacts** section.
- It also runs by itself whenever the app's code or Mac build files change,
  so a broken Mac build shows up straight away.

Each run also tests the image pipeline on synthetic images and checks that
the app launches.

**Which zip?** Apple menu → **About This Mac**: if it says *Chip: Apple M…*,
use `arm64`; if it says *Processor: Intel…*, use `x86_64`.

---

## Option B: Build it yourself on a Mac

### What you need

- A Mac running macOS 11 (Big Sur) or newer.
- Python 3.9 or newer **with tkinter**. The installer from
  [python.org](https://www.python.org/downloads/macos/) includes it. With
  Homebrew, run `brew install python python-tk`.
  Check with:
  ```
  python3 -c "import tkinter; print('ok')"
  ```

### Files you need

Clone or download the repository, or put these files in one folder:
- `FluorescenceChannelMerger.py` — the app itself
- `requirements.txt` — the Python packages it needs
- `build_mac.sh` — the build script
- `FluorescenceChannelMerger-mac.spec` — Mac build settings
- `FluorescenceChannelMerger.icns` — the Mac app icon
- `FluorescenceChannelMerger.ico` — used for the icon inside the window

### Build it

Open Terminal, `cd` into that folder, and run:

```bash
chmod +x build_mac.sh
./build_mac.sh
```

This will:
1. Create a private Python environment in `.venv-mac` (so nothing is
   installed into your system Python)
2. Install the required packages into it
3. Build `dist-mac/Fluorescence Channel Merger.app`
4. Zip it as `dist-mac/FluorescenceChannelMerger-v4.0-macOS-<chip>.zip`

The first build takes a few minutes; later builds are quicker.

The app is built for the kind of Mac you build it on (Apple Silicon or
Intel). For the other kind, build on that kind of Mac, or use Option A.

---

## Running it

Unzip and double-click **Fluorescence Channel Merger**. You can drag it into
your **Applications** folder first if you like.

**Gatekeeper warning:** the app isn't signed with a paid Apple Developer
certificate, so the first time you open it macOS will say it can't verify
the developer, or that the app "is damaged". To get around this:

1. Try to open the app once, and click **Done** / **Cancel** on the warning.
2. Go to **System Settings → Privacy & Security**, scroll down, and click
   **Open Anyway** next to the message about Fluorescence Channel Merger.
   Confirm with your password. You only need to do this once.

On older macOS versions (before macOS 15), you can also right-click
(Control-click) the app → **Open** → **Open**.

If macOS still refuses ("is damaged and can't be opened"), clear the
download flag in Terminal, then open it normally:

```bash
xattr -dr com.apple.quarantine "/Applications/Fluorescence Channel Merger.app"
```

(Change the path if the app isn't in Applications.)

This is standard for any unsigned, in-house tool — not a sign anything's wrong.

---

## Sharing it

Share the **`.zip`**, not the `.app` itself: a `.app` is actually a folder,
and email, Slack and some cloud drives mangle it. Whoever receives the zip
just unzips it and gets a normal double-clickable app (they'll see the same
Gatekeeper warning once).

## Notes

- Everything works the same as on Windows: drag and drop folders, light and
  dark mode (follows your Mac's appearance setting), cell counting, and
  outputs. Output folders open in Finder when a batch finishes.
- To change the icon, replace `FluorescenceChannelMerger.icns` (must be
  `.icns` format) before building.
