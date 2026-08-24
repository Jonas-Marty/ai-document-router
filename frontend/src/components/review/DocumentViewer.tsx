import { FileText, RotateCw, X, ZoomIn, ZoomOut } from "lucide-react";
import { useState } from "react";
import { Document, Page } from "react-pdf";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { documentContentUrl } from "@/hooks/useDocument";
import { useElementWidth } from "@/hooks/useElementWidth";
import { cn } from "@/lib/utils";
import "./pdfWorker";

export interface DocumentViewerProps {
  documentId: string;
  filename: string;
  mimeType: string;
  fileSizeBytes: number;
  pageCount: number | null;
}

const SWIPE_THRESHOLD_PX = 50;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function isPdf(mimeType: string): boolean {
  return mimeType === "application/pdf";
}

/** SPEC 8.3/8.4: the document viewer. A PDF renders via react-pdf (pdf.js); anything else
 * (SPEC scopes this to scanned documents, so in practice images) falls back to a plain
 * <img>, sharing the same toolbar minus paging. */
export function DocumentViewer(props: DocumentViewerProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <CompactCard {...props} onOpen={() => setOpen(true)} />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="bottom" className="gap-0 rounded-none p-0 data-[side=bottom]:h-dvh">
          <SheetTitle className="sr-only">{props.filename}</SheetTitle>
          <FullScreenViewer {...props} onClose={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}

function CompactCard(props: DocumentViewerProps & { onOpen: () => void }) {
  const { documentId, filename, fileSizeBytes, pageCount, mimeType, onOpen } = props;
  const url = documentContentUrl(documentId);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-3 rounded-lg border border-border bg-card p-2 text-left transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex h-20 w-16 shrink-0 items-center justify-center overflow-hidden rounded border border-border bg-muted">
        {isPdf(mimeType) ? (
          <Document
            file={url}
            loading={<FileText className="size-6 text-muted-foreground" />}
            error={<FileText className="size-6 text-muted-foreground" />}
          >
            <Page pageNumber={1} width={64} renderTextLayer={false} renderAnnotationLayer={false} />
          </Document>
        ) : (
          <img src={url} alt="" className="h-full w-full object-cover" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate font-mono text-sm">{filename}</p>
        <p className="text-xs text-muted-foreground">
          {pageCount !== null ? `${pageCount} page${pageCount === 1 ? "" : "s"} · ` : ""}
          {formatSize(fileSizeBytes)}
        </p>
      </div>
    </button>
  );
}

function FullScreenViewer(props: DocumentViewerProps & { onClose: () => void }) {
  const { documentId, filename, mimeType, pageCount, onClose } = props;
  const url = documentContentUrl(documentId);
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(pageCount ?? 1);
  const [rotation, setRotation] = useState(0);
  const [scale, setScale] = useState(1);
  const [loadError, setLoadError] = useState(false);
  const [touchStartX, setTouchStartX] = useState<number | null>(null);
  const { ref: containerRef, width: containerWidth } = useElementWidth<HTMLDivElement>();

  function goToPage(next: number) {
    setPage(Math.min(Math.max(next, 1), numPages));
  }

  // SPEC 8.4: "swipe paging". Pinch-zoom is left to the browser's native viewport gesture
  // (index.html's <meta viewport> doesn't disable user scaling) rather than a hand-rolled
  // gesture handler -- SPEC doesn't ask for a custom pinch implementation, and the native one
  // is the simpler option for a sheet the user can already scroll and dismiss by swipe-down.
  function onTouchStart(e: React.TouchEvent) {
    setTouchStartX(e.touches[0]?.clientX ?? null);
  }
  function onTouchEnd(e: React.TouchEvent) {
    if (touchStartX === null) return;
    const endX = e.changedTouches[0]?.clientX ?? touchStartX;
    const delta = endX - touchStartX;
    if (Math.abs(delta) > SWIPE_THRESHOLD_PX) {
      goToPage(delta < 0 ? page + 1 : page - 1);
    }
    setTouchStartX(null);
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex items-center justify-between gap-2 border-b border-border p-2">
        <p className="min-w-0 truncate font-mono text-sm">{filename}</p>
        <div className="flex shrink-0 items-center gap-1">
          {isPdf(mimeType) && (
            <>
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
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close viewer">
            <X />
          </Button>
        </div>
      </div>
      <div
        ref={containerRef}
        className="flex flex-1 items-center justify-center overflow-auto p-4"
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        {loadError ? (
          <ErrorState message="Couldn't load the file." onRetry={() => setLoadError(false)} />
        ) : isPdf(mimeType) ? (
          <Document
            file={url}
            loading={<LoadingState />}
            onLoadSuccess={({ numPages: n }) => setNumPages(n)}
            onLoadError={() => setLoadError(true)}
          >
            {containerWidth > 0 && (
              <Page
                pageNumber={page}
                width={containerWidth * scale}
                rotate={rotation}
                renderTextLayer={false}
                renderAnnotationLayer={false}
              />
            )}
          </Document>
        ) : (
          <img
            src={url}
            alt={filename}
            className={cn("max-h-full max-w-full object-contain")}
            style={{ transform: `scale(${scale}) rotate(${rotation}deg)` }}
            onError={() => setLoadError(true)}
          />
        )}
      </div>
      {isPdf(mimeType) && numPages > 1 && (
        <div className="flex items-center justify-center gap-3 border-t border-border p-2 text-sm">
          <Button
            variant="outline"
            size="sm"
            onClick={() => goToPage(page - 1)}
            disabled={page <= 1}
          >
            Prev
          </Button>
          <span className="tabular-nums">
            Page {page} of {numPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => goToPage(page + 1)}
            disabled={page >= numPages}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
