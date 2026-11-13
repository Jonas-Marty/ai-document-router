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

export interface RevertTarget {
  filename: string;
  folder: string;
}

/** SPEC 8.6: a confirmation naming the file and where it is now. The folder it goes back to
 * is not sent to the frontend; only the backend's revert computes it. */
export function RevertConfirmDialog({
  entry,
  onOpenChange,
  onConfirm,
  isPending,
}: {
  entry: RevertTarget | null;
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
              <span className="font-mono">{entry.filename}</span> moves out of{" "}
              <span className="font-mono">{entry.folder}</span> and back into the review queue.
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
