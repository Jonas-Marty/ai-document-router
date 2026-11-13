import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useEndpointStatuses } from "@/hooks/useInbox";
import { cn } from "@/lib/utils";

function Dot({ reachable }: { reachable: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "size-2 shrink-0 rounded-full",
        reachable ? "bg-emerald-500" : "bg-muted-foreground/40",
      )}
    />
  );
}

/** SPEC 8.6: which connections answer. Names from 1024 px; below that one dot that is green
 * only while every connection answers, with the details in its tooltip. */
export function ConnectionStatus() {
  const statuses = useEndpointStatuses().data ?? [];
  if (statuses.length === 0) return null;
  const up = statuses.filter((row) => row.reachable).length;
  const summary = statuses
    .map((row) => `${row.name}: ${row.reachable ? "reachable" : "not reachable"}`)
    .join(", ");

  return (
    <>
      <ul aria-label="Connections" className="hidden items-center gap-3 text-xs lg:flex">
        {statuses.map((row) => (
          <li
            key={row.id}
            className="flex items-center gap-1.5 text-muted-foreground"
            title={row.reachable ? "Reachable" : "Not reachable"}
          >
            <Dot reachable={row.reachable} />
            <span className="max-w-32 truncate">{row.name}</span>
            <span className="sr-only">{row.reachable ? "reachable" : "not reachable"}</span>
          </li>
        ))}
      </ul>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-md hover:bg-accent lg:hidden"
            aria-label={`Connections: ${up} of ${statuses.length} reachable. ${summary}`}
          >
            <Dot reachable={up === statuses.length} />
          </button>
        </TooltipTrigger>
        <TooltipContent>{summary}</TooltipContent>
      </Tooltip>
    </>
  );
}
