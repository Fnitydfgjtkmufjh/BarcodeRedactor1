# Barcode Redactor

A portable Windows desktop app that finds barcodes on **scanned** documents (PDF, JPG, PNG, TIFF)
and **permanently** removes them by overwriting the pixels. It's built for medical lab reports that
have been printed and then scanned in black and white. Everything runs offline on your own computer.

```
Select files  →  Detect / Redact  →  check the preview  →  Save Redacted Files
```

---

## 1. Using the app (end users)

1. Copy `BarcodeRedactor.exe` anywhere (desktop, USB stick) and double-click it.
   You don't need to install Python or anything else, and the app never connects to the internet.
2. Click **Select Files** or **Select Folder**, or drag files and folders onto the window.
3. Click **Redact Barcodes**.
   - In **Review before saving** mode (the default) every page is scanned, the first page that
     needs attention opens, and you can check each one.
   - In **Automatic** mode the files are redacted and saved in one step.
4. Look at the two previews. The left one is the original page with the detected regions, and
   the right one shows exactly what will be saved.
   - Box colours: **green** = decoded, **blue** = found visually, **orange** = low confidence,
     **purple** = drawn by you. A **dashed amber** box is a barcode-like area that was *not*
     redacted; double-click it to redact it.
   - **Draw box**: drag on the original page to add a region. Click a box to select it, drag it to
     move it, drag an edge or corner to resize it, and press **Delete** to remove it.
   - **Next to review** jumps to the next page that needs a human check. Tick **Page reviewed**
     once you're satisfied with a page.
5. Click **Save Redacted Files**. The output goes to the folder you choose: `report.pdf` becomes
   `report_redacted.pdf` and `scan.jpg` becomes `scan_redacted.jpg`. Original files are never
   modified and existing outputs are never overwritten (`_redacted_2` and so on). If you add a
   whole folder, its sub-folders are recreated in the output folder.
6. Each save also writes a CSV report, `redaction_report_<date>.csv`, with one row per page: file,
   page, number of barcode regions, detection method, review required, reviewed by user, reasons,
   error and output file. **A page where no barcode was found is always marked "Review required"**
   and is never assumed to be safe.

### Settings

| Setting | Default | Notes |
|---|---|---|
| Workflow | Review before saving | Automatic mode does detect, redact and save in one click |
| Fill colour | White | White or black solid fill |
| Padding | 1.5 mm | Extra margin around every barcode |
| Sensitivity | Balanced (50) | Lower means fewer false alarms; higher means fewer missed barcodes |
| Also redact QR / 2D codes | On | QR, DataMatrix, PDF417, Aztec |
| Include printed number under barcode | On | Also covers the digits printed directly below or above the bars (usually the sample ID) |
| PDF resolution | Match scan | Or force 200 / 300 / 600 dpi |
| PDF compression | Auto | B&W pages use lossless CCITT G4 (small files); grey and colour pages use JPEG. *Lossless* is larger |

### What "permanent" means here

- **Images:** the pixels inside each region are replaced with solid white or black. Pixel size and
  DPI stay the same. EXIF and other metadata are not copied.
- **PDFs:** every page is rendered and redacted, and then a **brand-new PDF** is built that contains
  only the redacted page images. No text layer, original image objects, annotations, hidden
  layers, attachments, document info or XMP metadata are carried over. Page sizes and orientation
  match the original.
- Detected barcode *contents* are never stored or written to the report. Only the symbology name
  is kept.

---

## 2. How detection works

Detection uses two methods, and their results are merged.

**Method A, decoding.** ZXing-C++ reads 1D and 2D codes on the page, plus a contrast-enhanced
copy. OpenCV's QR detector also finds QR codes that can't be decoded. For 1D codes ZXing reports
only a scan line, so the app then measures the full bar height from the image.

**Method B, visual analysis.** This matters because barcodes that have been printed and rescanned
often no longer decode.

1. The page is normalised to about 200 dpi and its local contrast is normalised, so faded toner
   looks like crisp print.
2. A *structure tensor* marks areas with strong edges that all point the same way (parallel bars).
   It runs at two threshold levels and two scales, and OpenCV's own 1D localiser adds more
   candidates.
3. Every candidate, from either method, is **verified in a rotation-corrected patch**. The bars
   must be solid across their whole height and consistent from row to row. There must be enough
   bars, the bar widths must vary (1D codes use several widths), and the height, span, bar density
   and aspect ratio must be plausible. These checks reject text, table rules, dashed lines,
   hatching and strings like `1111 IIII`.
4. The result is an **oriented box**, so tilted barcodes are covered tightly. Candidates that look
   barcode-like but fail verification are not redacted. Instead they show as dashed suspect boxes,
   and the page is sent to review.

A page is flagged for review when no barcode was found, when a detection has low confidence, when
a barcode-like area was left unredacted, or when processing failed. The app never claims a page is
clean just because one barcode was found.

### Measured accuracy (synthetic print-and-scan test set)

See [`docs/EVALUATION.md`](docs/EVALUATION.md), regenerated with `python tools/evaluate.py`. On
192 barcodes across 18 conditions (B&W, faded, rotated ±8°, 30°, vertical, JPEG artefacts, blur
and noise, shadows, 200/300/600 dpi, 0.19 mm modules, several barcodes per page, QR codes), every
barcode was redacted. There were 0 false-positive regions, including on 32 pages without barcodes
that were full of distractors. Example inputs and outputs are in [`samples/`](samples).

> These numbers come from synthetic pages. Before relying on the tool, try it on a sample of
> your own scanned reports and keep the review workflow on.

---

## 3. Building the Windows executable (developers)

You need Python only on the **build** machine. End users need nothing.

### Option A: GitHub Actions (no Windows machine needed)

1. Push this folder to a GitHub repository.
2. The **Build Windows executable** workflow (`.github/workflows/build-windows.yml`) runs on its
   own. It installs the dependencies, runs the whole test suite (including a headless GUI test),
   builds both packages, and runs `BarcodeRedactor.exe --self-test` on them.
3. Download the artifacts from the workflow run:
   - `BarcodeRedactor-exe`: the single-file `BarcodeRedactor.exe`
   - `BarcodeRedactor-portable-folder`: a zip of a folder that starts faster
4. Pushing a tag such as `v1.0.0` also attaches both to a GitHub Release.

### Option B: build locally on Windows 10/11

Install 64-bit Python 3.12 from python.org, then double-click `packaging\build_windows.bat` (or
run it from a terminal). It creates a virtual environment, installs the requirements, runs the
tests, builds `dist\BarcodeRedactor.exe` and `dist\BarcodeRedactor_portable\`, and self-tests
both.

Manual equivalent:

```bat
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt -r requirements-build.txt
python tools\make_icon.py
set QT_QPA_PLATFORM=offscreen && python -m pytest -q tests && set QT_QPA_PLATFORM=
pyinstaller --noconfirm --clean packaging\BarcodeRedactor.spec
set BR_ONEDIR=1 && pyinstaller --noconfirm --clean --workpath build\onedir packaging\BarcodeRedactor.spec
```

### Single file or folder?

| | `BarcodeRedactor.exe` (one file) | `BarcodeRedactor_portable\` (folder) |
|---|---|---|
| Copy and run | ✔ | ✔ (copy the whole folder) |
| Start-up | Slower (unpacks to a temp folder on every start) | Fast |
| Antivirus false alarms | More likely | Less likely |
| LGPL compliance when redistributing | Harder | Easier (Qt DLLs can be swapped) |

### Running from source (any OS)

```bash
pip install -r requirements.txt
python -m barcode_redactor                    # GUI
python -m barcode_redactor --self-test        # quick health check
python -m barcode_redactor --cli scans/ --out redacted/      # batch without GUI
```

`BarcodeRedactor.exe --self-test --out result.txt` checks that every bundled native library
(Qt, OpenCV, ZXing, PDFium) loads, and runs detection and redaction on a built-in synthetic report.
It exits with code 0 on success.

### Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
python tools/evaluate.py --seeds 8 --out docs/EVALUATION.md --samples samples
```

- `tests/test_detector.py` covers B&W, low contrast, rotation, JPEG, blur and noise, shadows,
  200/300/600 dpi, multiple and vertical barcodes, damage, QR codes, no-barcode pages and
  adversarial look-alikes.
- `tests/test_redaction.py` checks pixel replacement, padding, tilted boxes, and that a redacted
  barcode can no longer be detected.
- `tests/test_io_and_batch.py` covers image-only PDF output, metadata removal, page size and
  rotation, JPG/PNG/multipage TIFF, never overwriting files, folder batches, corrupt files, the CSV
  report, preserving manual edits, and cancellation.
- `tests/test_gui.py` drives the real window headlessly: adding files, detection, drawing and
  deleting boxes, review navigation and saving.

---

## 4. Project layout

```
barcode_redactor/
  app.py              entry point: GUI, --self-test, --cli
  core/detector.py    hybrid detector (ZXing + OpenCV structure-tensor analysis + verification)
  core/documents.py   page-by-page PDF (PDFium) and image (Pillow) access, never whole documents
  core/processing.py  redaction, output writers (image-only PDF, JPG/PNG/TIFF), CSV report
  core/models.py      Region / PageResult / DocumentEntry (normalised coordinates)
  core/synth.py       synthetic lab reports and print-and-scan degradations (tests, self-test)
  gui/                PySide6 window, zoomable editable preview, background workers, themes
packaging/            PyInstaller spec, Windows build script, version resource
tools/                evaluation script and icon generator
tests/                pytest suite
docs/EVALUATION.md    measured detection results
samples/              example input / detection / output images
```

Memory use stays flat for large batches. Pages are rendered one at a time, and only the region
coordinates are kept per page. Redacted PDF pages are written to temporary files as they are
produced, and the PDF is then assembled from those files.

## 5. Privacy

- The app makes no network calls and has no telemetry, login or cloud processing.
- Barcode contents are never stored, logged or written to the report.
- Temporary page images go into the system temp folder and are deleted after each file.
- Settings (options and the last-used folders) are stored in
  `%APPDATA%\BarcodeRedactor\BarcodeRedactor.ini`.
- File names appear in the CSV report. If your file names contain patient names, store the report
  as carefully as the documents themselves.

## 6. Known limitations

- 2D codes (QR, DataMatrix) are found by decoders and OpenCV's QR finder. A badly damaged 2D code
  that neither can locate may be missed, so turn the review workflow on for documents that carry
  them.
- Very small 1D barcodes scanned at 200 dpi or less, with module width below about 0.19 mm, may
  break up beyond recognition. Scan at 300 dpi when you can.
- A barcode printed directly on top of other content (overlapping text or another barcode) can be
  covered only partly. The page is usually flagged, so check it in the preview.
- "Include printed number" covers one text line of up to 5 mm that sits within 2.5 mm of the bars
  and inside the barcode's width.

## 7. Licences

The application code is under the MIT licence (`LICENSE`). Bundled third-party components and
their licences are listed in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md). PyMuPDF
(AGPL) was deliberately avoided, and PDF rendering uses PDFium (BSD and Apache) instead.
