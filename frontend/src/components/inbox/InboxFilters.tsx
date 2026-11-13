import { Search } from "lucide-react";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { cn } from "@/lib/utils";
import type { InboxQuery, InboxSort } from "@/services/api/types";

const SORTS: { value: InboxSort; label: string }[] = [
  { value: "newest", label: "Newest upload" },
  { value: "document_date", label: "Document date" },
  { value: "sender", label: "Sender" },
  { value: "needs_check", label: "Needs a check" },
];

// Radix Select refuses an empty value, so "no filter" needs a name of its own.
const ALL = "__all__";

export type FilterQuery = Pick<InboxQuery, "q" | "sender" | "folder" | "sort">;

/** The search, sender, folder and sort a list is shown with (SPEC 8.6, 8.3). Local UI state:
 * the Inbox and the review list each keep their own. */
export function useFilterState() {
  const [search, setSearch] = useState("");
  const [sender, setSender] = useState<string | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [sort, setSort] = useState<InboxSort>("newest");
  const q = useDebouncedValue(search.trim(), 300);

  const query: FilterQuery = {
    q: q || undefined,
    sender: sender ?? undefined,
    folder: folder ?? undefined,
    sort,
  };
  return {
    search,
    setSearch,
    sender,
    setSender,
    folder,
    setFolder,
    sort,
    setSort,
    query,
    filtered: q !== "" || sender !== null || folder !== null,
    clear() {
      setSearch("");
      setSender(null);
      setFolder(null);
    },
    /** Sender and folder options belong to one group; they do not carry over. */
    clearFacets() {
      setSender(null);
      setFolder(null);
    },
  };
}

export type FilterState = ReturnType<typeof useFilterState>;

/** The controls for a FilterState. `stacked` puts each on its own row, for the narrow list
 * pane on the review screen. */
export function InboxFilters({
  idPrefix,
  state,
  senders,
  folders,
  stacked = false,
}: {
  idPrefix: string;
  state: FilterState;
  senders: string[];
  folders: string[];
  stacked?: boolean;
}) {
  const cell = stacked ? "basis-full" : "min-w-0 flex-1 basis-36 sm:flex-none";
  const trigger = stacked ? "w-full" : "w-full sm:w-44";
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className={cn("relative min-w-0 basis-full", !stacked && "sm:flex-1 sm:basis-56")}>
        <Label htmlFor={`${idPrefix}-search`} className="sr-only">
          Search
        </Label>
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          id={`${idPrefix}-search`}
          type="search"
          className="pl-8"
          placeholder="Search names and document text"
          value={state.search}
          onChange={(event) => state.setSearch(event.target.value)}
        />
      </div>
      <FilterSelect
        id={`${idPrefix}-sender`}
        className={cell}
        triggerClassName={trigger}
        label="Sender"
        allLabel="All senders"
        value={state.sender}
        options={senders}
        onChange={state.setSender}
      />
      <FilterSelect
        id={`${idPrefix}-folder`}
        className={cell}
        triggerClassName={trigger}
        label="Folder"
        allLabel="All folders"
        value={state.folder}
        options={folders}
        onChange={state.setFolder}
      />
      <div className={cell}>
        <Label htmlFor={`${idPrefix}-sort`} className="sr-only">
          Sort
        </Label>
        <Select value={state.sort} onValueChange={(value) => state.setSort(value as InboxSort)}>
          <SelectTrigger id={`${idPrefix}-sort`} className={trigger} aria-label="Sort">
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
  );
}

function FilterSelect({
  id,
  className,
  triggerClassName,
  label,
  allLabel,
  value,
  options,
  onChange,
}: {
  id: string;
  className: string;
  triggerClassName: string;
  label: string;
  allLabel: string;
  value: string | null;
  options: string[];
  onChange: (value: string | null) => void;
}) {
  // A filter set before the options changed keeps showing what it filters by.
  const shown = value !== null && !options.includes(value) ? [value, ...options] : options;
  return (
    <div className={className}>
      <Label htmlFor={id} className="sr-only">
        {label}
      </Label>
      <Select value={value ?? ALL} onValueChange={(next) => onChange(next === ALL ? null : next)}>
        <SelectTrigger id={id} className={triggerClassName} aria-label={label}>
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
