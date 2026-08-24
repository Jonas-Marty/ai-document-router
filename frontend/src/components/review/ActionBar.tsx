import { Loader2, SkipForward, Trash2 } from "lucide-react";
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

export interface ActionBarProps {
  canApprove: boolean;
  isApproving: boolean;
  isSkipping: boolean;
  isTrashing: boolean;
  approveError: string | null;
  onApprove: () => void;
  onSkip: () => void;
  onTrash: () => void;
}

/** SPEC 8.4's sticky bottom action bar: full-width primary Approve, Skip and Trash as icon
 * buttons beside it, safe-area padded. Trash keeps its confirmation dialog. SPEC 8.8: a
 * failed approve shows its error right here, above the actions, in addition to a toast --
 * the toast alone would be easy to miss/dismiss and this is the one place the user is
 * guaranteed to be looking right after clicking Approve. */
export function ActionBar({
  canApprove,
  isApproving,
  isSkipping,
  isTrashing,
  approveError,
  onApprove,
  onSkip,
  onTrash,
}: ActionBarProps) {
  const [confirmTrash, setConfirmTrash] = useState(false);
  const busy = isApproving || isSkipping || isTrashing;

  return (
    <div className="sticky bottom-0 border-t border-border bg-background pb-[max(env(safe-area-inset-bottom),0.75rem)] pt-3">
      {approveError && (
        <p role="alert" className="mb-2 px-1 text-sm text-destructive">
          {approveError}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button className="h-11 min-w-0 flex-1" disabled={!canApprove || busy} onClick={onApprove}>
          {isApproving && <Loader2 className="animate-spin" />}
          Approve &amp; move
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="size-11 shrink-0"
          disabled={busy}
          onClick={onSkip}
          aria-label="Skip for now"
        >
          {isSkipping ? <Loader2 className="animate-spin" /> : <SkipForward />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-11 shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
          disabled={busy}
          onClick={() => setConfirmTrash(true)}
          aria-label="Move to trash"
        >
          {isTrashing ? <Loader2 className="animate-spin" /> : <Trash2 />}
        </Button>
      </div>

      <Dialog open={confirmTrash} onOpenChange={setConfirmTrash}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move to trash?</DialogTitle>
            <DialogDescription>
              The file moves to the trash folder. It isn't deleted and can be found there.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmTrash(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmTrash(false);
                onTrash();
              }}
            >
              Move to trash
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
