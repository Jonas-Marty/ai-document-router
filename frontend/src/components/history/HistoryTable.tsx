import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import type { HistoryEntry } from "@/services/api/types";
import { RevertTrigger } from "./RevertTrigger";

/** SPEC 8.6's desktop history table: processed time (relative, absolute on hover), original
 * filename, final filename, destination, an "edited" marker for overridden proposals, and a
 * Revert button (disabled + tooltip when the row no longer supports it -- see
 * RevertTrigger). Every path/filename is monospace per SPEC 9 -- spotting a naming
 * inconsistency is the point of this screen.
 *
 * `table-fixed` plus percentage widths on the header cells, not the shadcn default
 * `table-layout: auto`: auto layout sizes columns from content and lets `max-width` on a
 * `<td>` become a suggestion the renderer can still violate, so a long filename overflows its
 * cell and visually bleeds into the next column instead of truncating (confirmed in a real
 * browser -- jsdom's layout-free DOM never would have shown it). Fixed layout makes the
 * header row's widths the real column widths, so `truncate` on each cell actually clips. */
export function HistoryTable({
  items,
  onRevert,
}: {
  items: HistoryEntry[];
  onRevert: (entry: HistoryEntry) => void;
}) {
  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-28">Processed</TableHead>
          <TableHead className="w-1/4">Original name</TableHead>
          <TableHead className="w-1/4">Final name</TableHead>
          <TableHead className="w-1/4">Destination</TableHead>
          <TableHead className="w-24 text-right">Revert</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((entry) => (
          <TableRow key={entry.id}>
            <TableCell
              className="truncate text-muted-foreground"
              title={absoluteTime(entry.processed_at)}
            >
              {relativeTime(entry.processed_at)}
            </TableCell>
            <TableCell className="truncate font-mono" title={entry.original_filename}>
              {entry.original_filename}
            </TableCell>
            <TableCell className="truncate font-mono" title={entry.final_filename}>
              {entry.final_filename}
              {entry.was_overridden && (
                <Badge variant="outline" className="ml-2 align-middle font-sans">
                  Edited
                </Badge>
              )}
            </TableCell>
            <TableCell
              className="truncate font-mono text-muted-foreground"
              title={entry.final_folder_path}
            >
              {entry.final_folder_path}
            </TableCell>
            <TableCell className="text-right">
              <RevertTrigger revertible={entry.revertible} onClick={() => onRevert(entry)} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
