// biome-ignore-all lint/suspicious/noArrayIndexKey: PDF pages are an immutable, order-stable
// 1..numPages range for a document's lifetime -- there is no other identity to key them on.
import { RotateCw, ZoomIn, ZoomOut } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Document, Page } from "react-pdf";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";
import { documentContentUrl } from "@/hooks/useDocument";
import { useElementWidth } from "@/hooks/useElementWidth";
import "./pdfWorker";

export interface DesktopDocumentPaneHandle {
  nextPage: () => void;
  prevPage: () => void;
}

export interface DesktopDocumentPaneProps {
  documentId: string;
  filename: string;
  mimeType: string;
  fileSizeBytes: number;
  pageCount: number | null;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function isPdf(mimeType: string): boolean {
  return mimeType === "application/pdf";
}

/** SPEC 8.3's left pane: "react-pdf continuous scroll with a toolbar -- page indicator,
 * prev/next, zoom out/in, fit width, rotate. Images use the same toolbar minus paging.
 * Source filename and size shown above, muted." Continuous scroll (all pages stacked) is a
 * real difference from the mobile viewer's one-page-at-a-time sheet, not just a resize, so
 * this is its own component rather than a breakpoint branch inside DocumentViewer. */
export const DesktopDocumentPane = forwardRef<DesktopDocumentPaneHandle, DesktopDocumentPaneProps>(
  function DesktopDocumentPane({ documentId, filename, mimeType, fileSizeBytes, pageCount }, ref) {
    const url = documentContentUrl(documentId);
    const [numPages, setNumPages] = useState(pageCount ?? 1);
    const [currentPage, setCurrentPage] = useState(1);
    const [rotation, setRotation] = useState(0);
    const [scale, setScale] = useState(1);
    const [loadError, setLoadError] = useState(false);
    const { ref: containerRef, width: containerWidth } = useElementWidth<HTMLDivElement>();
    const pageRefs = useRef<(HTMLDivElement | null)[]>([]);

    function scrollToPage(page: number) {
      const clamped = Math.min(Math.max(page, 1), numPages);
      pageRefs.current[clamped - 1]?.scrollIntoView({ behavior: "smooth", block: "start" });
      setCurrentPage(clamped);
    }

    useImperativeHandle(ref, () => ({
      nextPage: () => scrollToPage(currentPage + 1),
      prevPage: () => scrollToPage(currentPage - 1),
    }));

    // Keeps the page indicator in sync with manual scrolling, not just the prev/next buttons.
    // containerRef.current is read fresh inside the effect rather than listed as a dependency
    // (it's a stable ref object); this must re-run when numPages changes (new page elements
    // to observe) but not on every scale/rotation change.
    // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above
    useEffect(() => {
      const container = containerRef.current;
      if (!container || !isPdf(mimeType)) return;
      const observer = new IntersectionObserver(
        (entries) => {
          const mostVisible = entries
            .filter((e) => e.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
          if (mostVisible) {
            const index = pageRefs.current.indexOf(mostVisible.target as HTMLDivElement);
            if (index !== -1) setCurrentPage(index + 1);
          }
        },
        { root: container, threshold: [0.5] },
      );
      for (const el of pageRefs.current) {
        if (el) observer.observe(el);
      }
      return () => observer.disconnect();
    }, [numPages, mimeType]);

    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between gap-2 border-b border-border p-2">
          <div className="min-w-0">
            <p className="truncate font-mono text-sm text-muted-foreground">{filename}</p>
            <p className="text-xs text-muted-foreground">{formatSize(fileSizeBytes)}</p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {isPdf(mimeType) && (
              <>
                <span className="mr-1 text-sm tabular-nums text-muted-foreground">
                  Page {currentPage} of {numPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={currentPage <= 1}
                  onClick={() => scrollToPage(currentPage - 1)}
                >
                  Prev
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={currentPage >= numPages}
                  onClick={() => scrollToPage(currentPage + 1)}
                >
                  Next
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setScale((s) => Math.max(0.5, s - 0.25))}
                  aria-label="Zoom out"
                >
                  <ZoomOut />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setScale((s) => Math.min(3, s + 0.25))}
                  aria-label="Zoom in"
                >
                  <ZoomIn />
                </Button>
                <Button variant="outline" size="sm" onClick={() => setScale(1)}>
                  Fit width
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setRotation((r) => (r + 90) % 360)}
                  aria-label="Rotate"
                >
                  <RotateCw />
                </Button>
              </>
            )}
          </div>
        </div>
        <div ref={containerRef} className="flex-1 overflow-auto bg-muted/20 p-4">
          {loadError ? (
            <ErrorState message="Couldn't load the file." onRetry={() => setLoadError(false)} />
          ) : isPdf(mimeType) ? (
            <Document
              file={url}
              loading={<LoadingState />}
              onLoadSuccess={({ numPages: n }) => setNumPages(n)}
              onLoadError={() => setLoadError(true)}
            >
              {containerWidth > 0 &&
                // Pages are an immutable, order-stable 1..numPages range for this document's
                // lifetime -- there is no other identity for a PDF page to key on.
                Array.from({ length: numPages }, (_, i) => (
                  <div
                    key={`page-${i + 1}`}
                    ref={(el) => {
                      pageRefs.current[i] = el;
                    }}
                    className="mb-4 flex justify-center last:mb-0"
                  >
                    <Page
                      pageNumber={i + 1}
                      width={containerWidth * scale}
                      rotate={rotation}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                    />
                  </div>
                ))}
            </Document>
          ) : (
            <img
              src={url}
              alt={filename}
              className="mx-auto max-w-full object-contain"
              style={{ transform: `scale(${scale}) rotate(${rotation}deg)` }}
              onError={() => setLoadError(true)}
            />
          )}
        </div>
      </div>
    );
  },
);
