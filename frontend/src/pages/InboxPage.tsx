import { Loader2, Search, Sparkles, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { AnalyseWithDialog } from "@/components/inbox/AnalyseWithDialog";
import { InboxCardView } from "@/components/inbox/InboxCardView";
import { RevertConfirmDialog, type RevertTarget } from "@/components/inbox/RevertConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  useAnalyseDocuments,
  useInbox,
  useRevertHistoryEntry,
  useTrashDocuments,
} from "@/hooks/useInbox";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { InboxCard, InboxGroup, InboxSort } from "@/services/api/types";

const GROUPS: { value: InboxGroup; label: string }[] = [
  { value: "analyse", label: "To analyse" },
  { value: "ready", label: "Ready for review" },
  { value: "done", label: "Done" },
  { value: "trashed", label: "Trashed" },
];

const SORTS: { value: InboxSort; label: string }[] = [
  { value: "newest", label: "Newest upload" },
  { value: "document_date", label: "Document date" },
  { value: "sender", label: "Sender" },
  { value: "needs_check", label: "Needs a check" },
];

const EMPTY: Record<InboxGroup, { title: string; description?: string }> = {
  analyse: { title: "Nothing waiting for analysis." },
  ready: { title: "Nothing to review.", description: "New scans appear here automatically." },
  done: { title: "Nothing filed yet." },
  trashed: { title: "Nothing in the trash." },
};

// Radix Select refuses an empty value, so "no filter" needs a name of its own.
const ALL = "__all__";

function isGroup(value: string | null): value is InboxGroup {
  return GROUPS.some((group) => group.value === value);
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** SPEC 8.6: every document in four groups, searchable, with bulk analyse and trash. */
export default function InboxPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const group: InboxGroup = isGroup(tabParam) ? tabParam : "ready";

  const [search, setSearch] = useState("");
  const [sender, setSender] = useState<string | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [sort, setSort] = useState<InboxSort>("newest");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [analyseWithOpen, setAnalyseWithOpen] = useState(false);
  const [confirmTrash, setConfirmTrash] = useState(false);
  const [revertCard, setRevertCard] = useState<InboxCard | null>(null);
  const query = useDebouncedValue(search.trim(), 300);

  const inbox = useInbox({
    group,
    q: query || undefined,
    sender: sender ?? undefined,
    folder: folder ?? undefined,
    sort,
  });
  const pages = inbox.data?.pages ?? [];
  const latest = pages[pages.length - 1];
  const cards = useMemo(() => pages.flatMap((page) => page.items), [pages]);
  const counts = latest?.counts;
  const filtered = query !== "" || sender !== null || folder !== null;
  const selectable = group === "analyse" || group === "ready";
  const chosen = cards.filter((card) => selected.has(card.id)).map((card) => card.id);

  const analyse = useAnalyseDocuments();
  const trash = useTrashDocuments();
  const revert = useRevertHistoryEntry();

  function switchGroup(next: InboxGroup) {
    setSearchParams(next === "ready" ? {} : { tab: next });
    setSender(null);
    setFolder(null);
    setSelected(new Set());
  }

  function clearFilters() {
    setSearch("");
    setSender(null);
    setFolder(null);
  }

  function toggle(id: string, on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const allSelected = cards.length > 0 && cards.every((card) => selected.has(card.id));

  function runAnalysis(configurationId?: string) {
    analyse.mutate(
      { document_ids: chosen, configuration_id: configurationId },
      {
        onSuccess: ({ queued }) => {
          toast.success(`Queued ${plural(queued, "document", "documents")} for analysis`);
          setSelected(new Set());
          setAnalyseWithOpen(false);
        },
        onError: (error) => toast.error(errorMessage(error, "Couldn't queue the analysis.")),
      },
    );
  }

  function runTrash() {
    trash.mutate(chosen, {
      onSuccess: ({ trashed, failed }) => {
        if (trashed > 0)
          toast.success(`Moved ${plural(trashed, "document", "documents")} to trash`);
        for (const failure of failed) toast.error(failure.message);
        setSelected(new Set());
      },
      onError: (error) => toast.error(errorMessage(error, "Couldn't trash the documents.")),
      onSettled: () => setConfirmTrash(false),
    });
  }

  function runRevert() {
    const entryId = revertCard?.history_entry_id;
    if (!entryId) return;
    revert.mutate(entryId, {
      onSuccess: () => toast.success("Reverted — back in the queue"),
      onError: (error) => toast.error(errorMessage(error, "Couldn't revert this document.")),
      onSettled: () => setRevertCard(null),
    });
  }

  const revertTarget: RevertTarget | null =
    revertCard?.name && revertCard.folder
      ? { filename: revertCard.name, folder: revertCard.folder }
      : null;

  return (
    <div className={cn("mx-auto max-w-7xl space-y-4 p-4", chosen.length > 0 && "pb-24")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Inbox</h1>
        <WaitingBanner group={group} counts={counts} onOpen={switchGroup} />
      </div>

      <GroupTabs group={group} counts={counts} onChange={switchGroup} />

      <div className="flex flex-wrap items-end gap-2">
        <div className="relative min-w-0 basis-full sm:flex-1 sm:basis-56">
          <Label htmlFor="inbox-search" className="sr-only">
            Search
          </Label>
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            id="inbox-search"
            type="search"
            className="pl-8"
            placeholder="Search names and document text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <FilterSelect
          id="inbox-sender"
          label="Sender"
          allLabel="All senders"
          value={sender}
          options={latest?.senders ?? []}
          onChange={setSender}
        />
        <FilterSelect
          id="inbox-folder"
          label="Folder"
          allLabel="All folders"
          value={folder}
          options={latest?.folders ?? []}
          onChange={setFolder}
        />
        <div className="min-w-0 flex-1 basis-36 sm:flex-none">
          <Label htmlFor="inbox-sort" className="sr-only">
            Sort
          </Label>
          <Select value={sort} onValueChange={(value) => setSort(value as InboxSort)}>
            <SelectTrigger id="inbox-sort" className="w-full sm:w-44" aria-label="Sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORTS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {selectable && cards.length > 0 && (
        <label className="flex w-fit items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={allSelected}
            onChange={(event) =>
              setSelected(event.target.checked ? new Set(cards.map((card) => card.id)) : new Set())
            }
          />
          Select all on page
        </label>
      )}

      {inbox.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
          {[0, 1, 2].map((key) => (
            <Skeleton key={key} className="h-36 w-full" />
          ))}
        </div>
      ) : inbox.isError ? (
        <ErrorState
          message={errorMessage(inbox.error, "Couldn't load the Inbox.")}
          onRetry={() => inbox.refetch()}
        />
      ) : cards.length === 0 ? (
        filtered ? (
          <EmptyState
            title="No documents match."
            action={{ label: "Clear filters", onClick: clearFilters }}
          />
        ) : (
          <EmptyState
            title={EMPTY[group].title}
            description={EMPTY[group].description}
            action={
              group === "ready"
                ? { label: "Check for new documents", onClick: () => inbox.refetch() }
                : undefined
            }
          />
        )
      ) : (
        <>
          <ul
            aria-label={GROUPS.find((row) => row.value === group)?.label}
            className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
          >
            {cards.map((card) => (
              <InboxCardView
                key={card.id}
                card={card}
                group={group}
                selected={selected.has(card.id)}
                onSelectedChange={selectable ? (on) => toggle(card.id, on) : undefined}
                onRevert={
                  !selectable && card.history_entry_id ? () => setRevertCard(card) : undefined
                }
              />
            ))}
          </ul>
          {inbox.hasNextPage && (
            <div className="flex justify-center">
              <Button
                type="button"
                variant="outline"
                disabled={inbox.isFetchingNextPage}
                onClick={() => inbox.fetchNextPage()}
              >
                {inbox.isFetchingNextPage && (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                )}
                Load more
              </Button>
            </div>
          )}
        </>
      )}

      {selectable && chosen.length > 0 && (
        <div
          role="toolbar"
          aria-label="Selected documents"
          className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/95 p-3 backdrop-blur"
        >
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2">
            <span className="mr-auto text-sm font-medium">{chosen.length} selected</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="Clear selection"
              onClick={() => setSelected(new Set())}
            >
              <X aria-hidden="true" />
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={analyse.isPending}
              onClick={() => runAnalysis()}
            >
              <Sparkles aria-hidden="true" />
              {group === "ready" ? "Re-analyse" : "Analyse"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setAnalyseWithOpen(true)}
            >
              Analyse with…
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setConfirmTrash(true)}
            >
              <Trash2 aria-hidden="true" />
              Trash
            </Button>
          </div>
        </div>
      )}

      {/* Mounted only while open: the configurations and the estimate are not worth fetching
          for a dialog nobody opened. */}
      {analyseWithOpen && (
        <AnalyseWithDialog
          documentIds={chosen}
          open
          onOpenChange={setAnalyseWithOpen}
          isPending={analyse.isPending}
          onRun={runAnalysis}
        />
      )}

      <Dialog
        open={confirmTrash}
        onOpenChange={(open) => !trash.isPending && setConfirmTrash(open)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Trash {plural(chosen.length, "document", "documents")}?</DialogTitle>
            <DialogDescription>
              They move to the trash folder. Nothing is deleted, and each can be reverted from the
              Trashed tab.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmTrash(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={trash.isPending}
              onClick={runTrash}
            >
              {trash.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              Trash
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <RevertConfirmDialog
        entry={revertTarget}
        onOpenChange={(open) => !open && setRevertCard(null)}
        onConfirm={runRevert}
        isPending={revert.isPending}
      />
    </div>
  );
}

function GroupTabs({
  group,
  counts,
  onChange,
}: {
  group: InboxGroup;
  counts: Record<InboxGroup, number> | undefined;
  onChange: (group: InboxGroup) => void;
}) {
  const label = (row: (typeof GROUPS)[number]) =>
    counts ? `${row.label} (${counts[row.value]})` : row.label;
  return (
    <>
      <div
        role="tablist"
        aria-label="Groups"
        className="hidden gap-1 border-b border-border sm:flex"
      >
        {GROUPS.map((row) => (
          <button
            key={row.value}
            type="button"
            role="tab"
            aria-selected={row.value === group}
            className={cn(
              "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium",
              row.value === group
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => onChange(row.value)}
          >
            {row.label}
            {counts && (
              <span className="rounded bg-secondary px-1.5 py-0.5 text-xs tabular-nums text-secondary-foreground">
                {counts[row.value]}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="sm:hidden">
        <Label htmlFor="inbox-group" className="sr-only">
          Group
        </Label>
        <Select value={group} onValueChange={(value) => onChange(value as InboxGroup)}>
          <SelectTrigger id="inbox-group" className="w-full" aria-label="Group">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GROUPS.map((row) => (
              <SelectItem key={row.value} value={row.value}>
                {label(row)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </>
  );
}

/** SPEC 8.6: while another tab is open, say what is waiting in the open groups. */
function WaitingBanner({
  group,
  counts,
  onOpen,
}: {
  group: InboxGroup;
  counts: Record<InboxGroup, number> | undefined;
  onOpen: (group: InboxGroup) => void;
}) {
  if (!counts) return null;
  const parts: { group: InboxGroup; text: string }[] = [];
  if (group !== "ready" && counts.ready > 0) {
    parts.push({ group: "ready", text: `${counts.ready} ready for review` });
  }
  if (group !== "analyse" && counts.analyse > 0) {
    parts.push({ group: "analyse", text: `${counts.analyse} to analyse` });
  }
  if (parts.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-1 rounded-md bg-accent px-3 py-1.5 text-sm text-accent-foreground">
      {parts.map((part, index) => (
        <span key={part.group}>
          {index > 0 && " · "}
          <Link
            to={part.group === "ready" ? "/" : `/?tab=${part.group}`}
            className="font-medium underline-offset-4 hover:underline"
            onClick={(event) => {
              event.preventDefault();
              onOpen(part.group);
            }}
          >
            {part.text}
          </Link>
        </span>
      ))}
    </p>
  );
}

function FilterSelect({
  id,
  label,
  allLabel,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  allLabel: string;
  value: string | null;
  options: string[];
  onChange: (value: string | null) => void;
}) {
  // A filter set before the options changed keeps showing what it filters by.
  const shown = value !== null && !options.includes(value) ? [value, ...options] : options;
  return (
    <div className="min-w-0 flex-1 basis-36 sm:flex-none">
      <Label htmlFor={id} className="sr-only">
        {label}
      </Label>
      <Select value={value ?? ALL} onValueChange={(next) => onChange(next === ALL ? null : next)}>
        <SelectTrigger id={id} className="w-full sm:w-44" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{allLabel}</SelectItem>
          {shown.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
