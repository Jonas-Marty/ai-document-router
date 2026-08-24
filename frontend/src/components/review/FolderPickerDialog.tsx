import { useEffect, useState } from "react";
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
import { useSettings } from "@/hooks/useSettings";
import { isWithinAllowedRoot } from "@/lib/naming";

export interface FolderPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onSelect: (path: string) => void;
}

/** M7 stopgap for SPEC 8.5's folder picker: a validated text field rather than the lazy
 * tree/type-to-filter/create-folder UI M8 builds. Kept as a dialog on every breakpoint for
 * now -- M8 splits this into a dialog (desktop) and full-screen sheet (mobile) when it
 * replaces the body with the real tree, but the trigger and this validation stay the same.
 * SPEC 7.2 ("must be inside one of allowed_root_folders") is enforced here too so the field
 * can't be typed into an unusable state -- this is feedback only, the real boundary is
 * server-side (CLAUDE.md rule 3). */
export function FolderPickerDialog({
  open,
  onOpenChange,
  value,
  onSelect,
}: FolderPickerDialogProps) {
  const { data: settings } = useSettings();
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    if (open) setDraft(value);
  }, [open, value]);

  const allowedRoots = settings?.allowed_root_folders ?? [];
  const trimmed = draft.trim();
  const error =
    trimmed.length === 0
      ? "Enter a folder path."
      : allowedRoots.length > 0 && !isWithinAllowedRoot(trimmed, allowedRoots)
        ? `Must be inside one of: ${allowedRoots.join(", ")}`
        : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose folder</DialogTitle>
          <DialogDescription>
            Enter the destination path. It must be inside one of the allowed root folders.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Input
            autoFocus
            className="font-mono"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-invalid={!!error}
            aria-label="Folder path"
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!!error}
            onClick={() => {
              onSelect(trimmed);
              onOpenChange(false);
            }}
          >
            Select
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
