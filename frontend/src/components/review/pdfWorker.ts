import { pdfjs } from "react-pdf";

// react-pdf re-exports the exact pdfjs-dist instance it uses internally; importing pdfjs
// from "pdfjs-dist" directly here (instead of from "react-pdf") would risk a second, separately
// resolved copy whose API version doesn't match the worker's, which pdf.js refuses to run
// under with an "API version does not match Worker version" error. `pdfjs-dist` is pinned as
// a direct dependency at the exact version react-pdf bundles (see package.json) specifically
// so this bare-specifier worker URL resolves to the same version pdfjs uses.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();
