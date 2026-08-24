import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { HistoryEntry } from "@/services/api/types";

/** SPEC 8.6: "Revert opens a confirmation naming the file and its destination." The API
 * doesn't return `source_folder_path` (only the backend's revert logic knows it, to compute
 * the MOVE target) -- "its destination" here is the file's *current* location, which is what
 * the confirmation can honestly show without inventing a path the frontend was never sent. */
export function RevertConfirmDialog({
  entry,
  onOpenChange,
  onConfirm,
  isPending,
}: {
  entry: HistoryEntry | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <Dialog open={entry !== null} onOpenChange={(open) => !isPending && onOpenChange(open)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revert this file?</DialogTitle>
          {entry && (
            <DialogDescription>
              <span className="font-mono">{entry.final_filename}</span> moves out of{" "}
              <span className="font-mono">{entry.final_folder_path}</span> and back into the review
              queue.
            </DialogDescription>
          )}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={isPending}>
            {isPending && <Loader2 className="animate-spin" />}
            Revert
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
