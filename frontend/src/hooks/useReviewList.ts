import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useFilterState } from "@/components/inbox/InboxFilters";
import { documentContentUrl, prefetchDocument, useDocument } from "./useDocument";
import { useInbox } from "./useInbox";

/** SPEC 8.8: the review list is the Inbox's Ready group with filters of its own, and the
 * current document is the one in the URL, else the list's first row.
 *
 * The URL is the only pointer. Moving on is a navigation the page makes in a mutation's
 * success handler, so a failed approve -- which never gets there -- cannot move it (CLAUDE.md
 * rule 7), and a refetch that reorders the list cannot either. */
export function useReviewList(requestedId: string | undefined) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const filters = useFilterState();
  const list = useInbox({ group: "ready", ...filters.query });
  const pages = list.data?.pages ?? [];
  const cards = pages.flatMap((page) => page.items);
  const latest = pages.at(-1);
  const currentId = requestedId ?? cards[0]?.id;
  const document = useDocument(currentId);

  /** The row below `id`, else the one above. A document outside the list -- opened from To
   * analyse, or filtered out -- hands over to the first row. */
  function nextAfter(id: string): string | undefined {
    const index = cards.findIndex((card) => card.id === id);
    if (index === -1) return cards.find((card) => card.id !== id)?.id;
    return cards[index + 1]?.id ?? cards[index - 1]?.id;
  }

  const nextId = currentId ? nextAfter(currentId) : undefined;

  // SPEC 8.8: the next row's document and content, ready before they are asked for.
  useEffect(() => {
    if (!nextId) return;
    void prefetchDocument(queryClient, nextId);
    const controller = new AbortController();
    fetch(documentContentUrl(nextId), { signal: controller.signal }).catch(() => {});
    return () => controller.abort();
  }, [nextId, queryClient]);

  /** Show `id`, or with none the list's first row. Replaces the history entry: stepping
   * through the list should not make Back walk through every document reviewed. */
  function show(id: string | undefined) {
    navigate(id ? `/review/${id}` : "/review", { replace: true });
  }

  return {
    filters,
    list,
    cards,
    readyCount: latest?.counts.ready ?? 0,
    senders: latest?.senders ?? [],
    folders: latest?.folders ?? [],
    currentId,
    document,
    inList: (id: string) => cards.some((card) => card.id === id),
    nextAfter,
    show,
  };
}
