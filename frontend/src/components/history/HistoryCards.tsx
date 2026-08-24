import { MoreVertical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import type { HistoryEntry } from "@/services/api/types";

/** SPEC 8.6's mobile stacked cards, revert in an overflow menu. A disabled menu item's own
 * greyed-out state is the affordance here rather than RevertTrigger's tooltip pattern --
 * tooltips are a hover construct with no equivalent on a touch device, so there's nothing for
 * a tooltip to add on this breakpoint that the disabled styling doesn't already say. */
export function HistoryCards({
  items,
  onRevert,
}: {
  items: HistoryEntry[];
  onRevert: (entry: HistoryEntry) => void;
}) {
  return (
    <div className="space-y-3">
      {items.map((entry) => (
        <Card key={entry.id} size="sm">
          <CardHeader>
            <CardTitle className="truncate font-mono text-sm" title={entry.final_filename}>
              {entry.final_filename}
            </CardTitle>
            <CardAction>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="Row actions">
                    <MoreVertical aria-hidden="true" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={!entry.revertible} onSelect={() => onRevert(entry)}>
                    Revert
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            <p className="truncate font-mono" title={entry.original_filename}>
              from {entry.original_filename}
            </p>
            <p className="truncate font-mono" title={entry.final_folder_path}>
              {entry.final_folder_path}
            </p>
            <div className="flex items-center gap-2">
              <span title={absoluteTime(entry.processed_at)}>
                {relativeTime(entry.processed_at)}
              </span>
              {entry.was_overridden && <Badge variant="outline">Edited</Badge>}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
