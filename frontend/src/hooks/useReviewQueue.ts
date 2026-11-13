import { useEffect, useState } from "react";
import type { Document } from "@/services/api/types";
import { useDocument } from "./useDocument";
import { useQueue } from "./useQueue";

function isOpen(document: Document): boolean {
  return document.status === "pending" || document.status === "skipped";
}

/** Owns which queue document is "current" by id, not by array index. An index would silently
 * point at a different document whenever the 60s background refetch (or a cache patch from a
 * mutation) reorders `items` -- exactly the kind of thing that must never happen mid-review
 * (CLAUDE.md rule 7). The pointer only ever moves for two reasons:
 *   1. The document it names is gone from the queue (approved/trashed -- or gone before the
 *      user ever picked one), so it falls back to whatever is now first. This is what SPEC
 *      8.8 means by "advance" for approve/trash: those mutations remove the document from
 *      the cache, and removal is what triggers this fallback.
 *   2. The caller explicitly calls `advancePast(id)` -- used for skip, which SPEC 8.8 says
 *      must "advance immediately" even though the document stays in the queue (just reordered
 *      to the back), so removal-triggered fallback doesn't apply.
 *   3. The caller explicitly calls `selectDocument(id)` -- the queue overview, where the
 *      person picks which document to work on out of order.
 * A failed mutation calls neither, so the pointer -- and the form built on top of it -- never
 * moves on failure.
 *
 * `requestedId` is the document named in the URL (/review/:id, SPEC 8.8). One beyond the
 * queue's first page is fetched on its own and "pinned" until it is filed, trashed or
 * skipped past; approve and trash write the document's new status into its cache, and a
 * document that is no longer open lets go by itself. */
export function useReviewQueue(requestedId?: string) {
  const queueQuery = useQueue();
  const items = queueQuery.data?.items ?? [];
  const [currentId, setCurrentId] = useState<string | null>(null);
  // Skipping past a pinned document lets go of it while the URL still names it.
  const [releasedId, setReleasedId] = useState<string | null>(null);
  const inQueue = requestedId !== undefined && items.some((doc) => doc.id === requestedId);
  // Derived, not state: it has to be in place in the very render the queue arrives, or the
  // page would first follow the queue's head into the URL and lose the request.
  const pinnedId =
    requestedId !== undefined && !inQueue && !queueQuery.isLoading && requestedId !== releasedId
      ? requestedId
      : null;
  const pinnedQuery = useDocument(pinnedId ?? undefined);
  const pinned = pinnedQuery.data && isOpen(pinnedQuery.data) ? pinnedQuery.data : undefined;

  // Only when the URL names a different document: following the current one into the URL
  // must not pull the pointer back after a skip.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reacts to the request alone
  useEffect(() => {
    if (requestedId !== undefined && inQueue) setCurrentId(requestedId);
  }, [requestedId, queueQuery.isLoading]);

  useEffect(() => {
    if (items.length === 0) {
      if (currentId !== null) setCurrentId(null);
      return;
    }
    const first = items[0];
    if (first && !items.some((doc) => doc.id === currentId)) {
      // Both effects can run in one commit; this one must not undo the URL's choice.
      setCurrentId(inQueue && currentId === null ? (requestedId ?? first.id) : first.id);
    }
  }, [items, currentId, inQueue, requestedId]);

  const currentDocument: Document | undefined = pinned ?? items.find((doc) => doc.id === currentId);

  /** Jump to a document the person picked out of the queue overview.
   *
   * An id that is no longer in the queue is ignored: the 60s refetch can remove one between
   * the list rendering and the click landing, and pointing the review pane at a document
   * that isn't there would blank it rather than saying anything useful. */
  function selectDocument(id: string) {
    if (items.some((doc) => doc.id === id)) {
      if (pinnedId !== null) setReleasedId(pinnedId);
      setCurrentId(id);
    }
  }

  function advancePast(id: string) {
    if (pinnedId === id) {
      setReleasedId(id);
      return;
    }
    setCurrentId((prev) => {
      if (prev !== id) return prev;
      const next = items.find((doc) => doc.id !== id);
      return next?.id ?? null;
    });
  }

  return {
    ...queueQuery,
    // A pinned document is still loading: the page must not say the queue is clear.
    isLoading: queueQuery.isLoading || (pinnedId !== null && pinnedQuery.isLoading),
    items,
    // Everything queued, not just what `items` holds: /queue is capped at QUEUE_LIMIT, and
    // the count the person is shown has to be the size of the backlog, not the page size.
    totalPending: queueQuery.data?.total_pending ?? items.length,
    currentDocument,
    selectDocument,
    advancePast,
  };
}
