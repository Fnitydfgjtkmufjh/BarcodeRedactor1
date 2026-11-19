# Third-party components bundled in BarcodeRedactor.exe

| Component | Purpose | Licence | Notes for redistribution |
|---|---|---|---|
| Python 3.12 runtime | interpreter | PSF License | permissive |
| PySide6 / Qt 6 (Essentials) | user interface | **LGPL-3.0** (Qt for Python, Qt) | Qt is dynamically linked. When you give the app to others, tell them it uses Qt/PySide6 under LGPL-3.0, link to the source (https://code.qt.io, https://download.qt.io) and let them replace the Qt libraries. The **portable-folder build** makes this straightforward (Qt DLLs are plain files); for the single-file exe, also provide the portable folder or the build files on request. |
| OpenCV (opencv-python-headless) | visual barcode detection | Apache-2.0 | include notice |
| ZXing-C++ (zxing-cpp) | barcode decoding | Apache-2.0 | include notice |
| pypdfium2 / PDFium | PDF rendering | Apache-2.0 / BSD-3-Clause (PDFium: BSD-3-Clause, Apache-2.0 parts) | include notice |
| img2pdf | writes image-only PDFs | **LGPL-3.0** | pure Python, used unmodified; same LGPL notice as Qt |
| pikepdf / qpdf | PDF writing backend of img2pdf | MPL-2.0 / Apache-2.0 | include notice; source at https://github.com/pikepdf/pikepdf |
| Pillow (+ libjpeg-turbo, libtiff, zlib, libpng, freetype) | image I/O | MIT-CMU (HPND); bundled libs under IJG/BSD/zlib/libpng/FTL licences | include notice |
| NumPy (+ OpenBLAS) | arrays | BSD-3-Clause | include notice |
| PyInstaller bootloader | packaging | GPL-2.0 **with bootloader exception** | the exception allows distributing the resulting exe under any licence |

Full licence texts are installed with each package (`site-packages/<package>-*.dist-info/`) and
can be collected for a release with `pip-licenses --with-license-file --format=markdown` in the
build environment.

Deliberately **not** used: PyMuPDF/MuPDF (AGPL-3.0 – would require releasing the whole app under
AGPL), pyzbar/ZBar (LGPL-2.1 native library, unmaintained).

This summary is provided for convenience and is not legal advice.
