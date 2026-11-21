# Offline Office preview assets

Run `npm ci --ignore-scripts` then `npm run build` here to reproduce the bundled
browser assets. Python/Android runtime has no Node or network dependency.

The DOCX renderer is docx-preview (Apache-2.0), using JSZip (MIT option).
The PPTX SVG renderer is @office-kit/pptx-preview and @office-kit/pptx (MIT),
using fflate (MIT). License texts and upstream notices ship with the assets.
The browser entry does not include the Node rasterizer or its bundled fonts.

These browser engines preserve much more document structure than text extraction,
but do not implement every Word/PowerPoint feature. They cannot guarantee pixel
parity. On Windows, available local Microsoft Office can export a fixed-page PDF;
an active Office session is left untouched. For universal fixed-page fidelity,
use a PDF exported by the document's author. Animations and editing are outside
the static preview contract. No documents are uploaded to third-party viewers.
