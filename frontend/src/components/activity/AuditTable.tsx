import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import type { AuditEvent } from "@/services/api/types";
import { AuditDetails, eventTokens } from "./AuditDetails";

/** SPEC 8.11's desktop event table. `table-fixed` for the same reason as HistoryTable: a long
 * email would otherwise push the columns around. The event column wraps instead of
 * truncating, since the summary sentence is the thing being read. */
export function AuditTable({ items }: { items: AuditEvent[] }) {
  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-28">When</TableHead>
          <TableHead className="w-1/5">Who</TableHead>
          <TableHead>Event</TableHead>
          <TableHead className="w-20 text-right">Tokens</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((event) => (
          <TableRow key={event.id} className="align-top">
            <TableCell
              className="truncate text-muted-foreground"
              title={absoluteTime(event.occurred_at)}
            >
              {relativeTime(event.occurred_at)}
            </TableCell>
            <TableCell className="truncate" title={event.actor_label}>
              {event.actor_label}
            </TableCell>
            <TableCell className="space-y-1 whitespace-normal">
              <p>{event.summary}</p>
              <AuditDetails event={event} />
            </TableCell>
            <TableCell className="text-right text-muted-foreground tabular-nums">
              {eventTokens(event)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
