import { Loader2, Search } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useInbox } from "@/hooks/useInbox";

/** SPEC 8.13a: Done documents to add, searchable, each with a checkbox. Mounted only while
 * open, so the search and the ticks start empty each time. */
export function AddFromDoneDialog({
  open,
  onOpenChange,
  isAdding,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isAdding: boolean;
  onAdd: (ids: string[]) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] flex-col">
        {open && (
          <DonePicker isAdding={isAdding} onAdd={onAdd} onCancel={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function DonePicker({
  isAdding,
  onAdd,
  onCancel,
}: {
  isAdding: boolean;
  onAdd: (ids: string[]) => void;
  onCancel: () => void;
}) {
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), 300);
  const done = useInbox({ group: "done", q: q || undefined });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const cards = done.data?.pages.flatMap((page) => page.items) ?? [];

  function toggle(id: string) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add from Done</DialogTitle>
        <DialogDescription>
          Each document is tested against the name and folder you approved.
        </DialogDescription>
      </DialogHeader>
      <div className="relative">
        <Label htmlFor="done-search" className="sr-only">
          Search
        </Label>
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          id="done-search"
          type="search"
          className="pl-8"
          placeholder="Search names and document text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {done.isLoading ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : done.isError ? (
          <p className="text-sm text-muted-foreground">Couldn't load the Done documents.</p>
        ) : cards.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {q ? "Nothing filed matches." : "Nothing has been filed yet."}
          </p>
        ) : (
          <ul className="space-y-1">
            {cards.map((card) => (
              <li key={card.id}>
                <label className="flex items-start gap-2 rounded-md px-1 py-1.5 hover:bg-accent/40">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 shrink-0 accent-primary"
                    checked={picked.has(card.id)}
                    onChange={() => toggle(card.id)}
                  />
                  <span className="min-w-0">
                    <span className="block font-mono text-xs break-words sm:text-sm">
                      {card.name ?? card.original_filename}
                    </span>
                    <span className="block font-mono text-xs break-words text-muted-foreground">
                      {card.folder}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {done.hasNextPage && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2 w-full"
            disabled={done.isFetchingNextPage}
            onClick={() => done.fetchNextPage()}
          >
            Load more
          </Button>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="button"
          disabled={picked.size === 0 || isAdding}
          onClick={() => onAdd([...picked])}
        >
          {isAdding && <Loader2 className="animate-spin" aria-hidden="true" />}
          {picked.size ? `Add ${picked.size}` : "Add"}
        </Button>
      </DialogFooter>
    </>
  );
}
