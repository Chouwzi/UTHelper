# ADR 0005: Local fixed-page and offline Office previews

Date: 2026-11-21. Status: accepted for the Courses topic branch.

The former DOCX/PPTX preview reconstructed extracted text, tables and images.
This cannot retain Word pagination or authored PowerPoint shape placement, and
the user observed visibly incorrect originals. External browser launch must be
an explicit header action, including when no authenticated Moodle client exists.

Use bounded, tokenized loopback URLs and pinned offline browser engines for
modern OOXML. DOCX uses docx-preview 0.4.1; PPTX uses MIT-licensed @office-kit/pptx
0.28.0 and its 0.21.0 SVG preview. Vendor licenses, notices, hashes and a locked
development-only build. Viewer CSP permits only local scripts and document
images/fonts; no document is submitted to a cloud conversion/viewer service.
The standalone viewer accepts only a same-origin tokenized document URL.
HTML attachment previews retain their separate script-free CSP.

On desktop Windows, optionally export the original through installed Microsoft
Office to PDF off the UI thread. Macros are disabled, Word opens read-only with
link updates disabled, and an active Office session is not reused. Temporary
inputs and PDF are removed after bounded conversion. On timeout only an identified
new Office PID matching the expected executable and creation time can be stopped;
never kill by process name. Unavailable, password-protected or failed exports
retain the offline viewer. Keep save/external actions bound to original bytes.
Windows opens the fixed-page PDF in WebView2 first; the existing internal PDF
renderer remains available where native WebViews cannot display PDFs.

The adapter owns WebView bounds, suspension and disposal instead of treating a
plain host adapter as a Flet Control. Mobile PDF search collapses, bookmarks use a
modal sheet, page images support pinch zoom, and wide fallback tables scroll.

Limits: browser preview is not pixel parity with Office. Office PDF preserves
static Office layout but not animations, editing or every interactive behavior.
Fonts available on the device and authored PDF output affect parity. Universal
100% Word/PowerPoint fidelity cannot be promised without the original Office
engine/fonts or a PDF exported by the author.

References:

- [Word ExportAsFixedFormat](https://learn.microsoft.com/en-us/office/vba/api/word.document.exportasfixedformat)
- [PowerPoint PDF SaveAs](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentation.saveas)
- [docx-preview capabilities and page break limits](https://github.com/VolodymyrBaydalka/docxjs)
- [PPTX SVG renderer fidelity limits](https://github.com/office-kit/pptx/blob/main/packages/preview/README.md)

Validation: actual local Office exported synthetic two-page DOCX/PPTX files;
local headless Chromium loaded both original packages at 390 px, retained page/
slide placement, navigated slides and notes, and reported no page errors. These
are local fixture checks, not acceptance of an arbitrary user document or Android
device certification. Private captures stay outside Git.
