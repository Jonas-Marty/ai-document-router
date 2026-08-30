import { useEffect, useState } from "react";
import type { Document } from "@/services/api/types";
import { useQueue } from "./useQueue";

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
 * moves on failure. */
export function useReviewQueue() {
  const queueQuery = useQueue();
  const items = queueQuery.data?.items ?? [];
  const [currentId, setCurrentId] = useState<string | null>(null);

  useEffect(() => {
    if (items.length === 0) {
      if (currentId !== null) setCurrentId(null);
      return;
    }
    const first = items[0];
    if (first && !items.some((doc) => doc.id === currentId)) {
      setCurrentId(first.id);
    }
  }, [items, currentId]);

  const currentDocument: Document | undefined = items.find((doc) => doc.id === currentId);

  /** Jump to a document the person picked out of the queue overview.
   *
   * An id that is no longer in the queue is ignored: the 60s refetch can remove one between
   * the list rendering and the click landing, and pointing the review pane at a document
   * that isn't there would blank it rather than saying anything useful. */
  function selectDocument(id: string) {
    if (items.some((doc) => doc.id === id)) setCurrentId(id);
  }

  function advancePast(id: string) {
    setCurrentId((prev) => {
      if (prev !== id) return prev;
      const next = items.find((doc) => doc.id !== id);
      return next?.id ?? null;
    });
  }

  return {
    ...queueQuery,
    items,
    // Everything queued, not just what `items` holds: /queue is capped at QUEUE_LIMIT, and
    // the count the person is shown has to be the size of the backlog, not the page size.
    totalPending: queueQuery.data?.total_pending ?? items.length,
    currentDocument,
    selectDocument,
    advancePast,
  };
}
