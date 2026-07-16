# Third-party software notices

DeepCode Desktop includes open-source Python, JavaScript, Rust, and platform
runtime components. Their package names, versions, license expressions, and
source metadata are recorded by the locked manifests:

- `desktop/sidecar-requirements.lock`
- `desktop/package-lock.json`
- `desktop/src-tauri/Cargo.lock`

The release pipeline runs `desktop/scripts/audit-licenses.py` against all three
resolved graphs and retains its machine-readable report as a build artifact.
The current dependency set uses permissive or weak-copyleft licenses including
MIT, Apache-2.0, BSD, ISC, MPL-2.0, Unicode, Python, Zlib, and CC0 variants.

PyInstaller is a build-time tool distributed under GPLv2-or-later with its
documented exception permitting distribution of non-free bundled programs.
Docling is optional and is not included in the Desktop sidecar; the packaged
baseline document converters use the Python standard library plus pypdf.

Operating-system WebViews and other system libraries retain the notices and
license terms supplied by their platform vendors. This file is informational;
the license text and source URL published by each dependency remain
authoritative.
