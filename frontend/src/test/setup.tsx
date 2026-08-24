import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";

// pdf.js needs a real browser (canvas, Worker) that jsdom doesn't provide, so every test that
// imports DocumentViewer (transitively, react-pdf) would fail before hitting jsdom's own
// limitations. This stub is enough for tests that only assert around the viewer, not on PDF
// rendering itself -- that's covered by real Chromium instead (see DECISIONS.md).
vi.mock("react-pdf", () => ({
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children?: React.ReactNode;
    onLoadSuccess?: (doc: { numPages: number }) => void;
  }) => {
    onLoadSuccess?.({ numPages: 1 });
    return <div data-testid="pdf-document">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid="pdf-page">Page {pageNumber}</div>
  ),
  pdfjs: { GlobalWorkerOptions: {} },
}));

// jsdom doesn't implement ResizeObserver either (useElementWidth, used by DocumentViewer,
// needs it). A no-op is enough for tests that don't assert on measured width -- real sizing
// is verified in a real browser (see DECISIONS.md).
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom doesn't implement matchMedia. ThemeProvider needs it to resolve "system", and
// tests that don't care about theme still mount it (it's app-wide), so this has to be a
// global default rather than something each test file stubs individually.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
