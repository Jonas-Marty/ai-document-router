import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import type { Document } from "@/services/api/types";
import { QueueList } from "./QueueList";

export interface QueuePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: Document[];
  totalPending: number;
  currentId: string | undefined;
  onSelect: (id: string) => void;
}

/** The queue overview, in the chrome this app already uses for a secondary surface: a dialog
 * on desktop, a full-height sheet on mobile (same split as FolderPicker, SPEC 8.5).
 *
 * Picking a document closes the panel. Leaving it open would put the list on top of the
 * document it just navigated to, which is the one thing the person now wants to look at. */
export function QueuePanel({
  open,
  onOpenChange,
  items,
  totalPending,
  currentId,
  onSelect,
}: QueuePanelProps) {
  const isDesktop = useIsDesktop();
  const heading = `Open documents (${totalPending})`;

  function choose(id: string) {
    onSelect(id);
    onOpenChange(false);
  }

  const body = (
    <QueueList items={items} totalPending={totalPending} currentId={currentId} onSelect={choose} />
  );

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex h-[32rem] max-w-lg flex-col gap-0 p-0">
          <DialogHeader className="p-4 pb-3">
            <DialogTitle>{heading}</DialogTitle>
          </DialogHeader>
          {body}
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        className="flex flex-col gap-0 rounded-none p-0 data-[side=bottom]:h-dvh"
      >
        <SheetHeader className="p-4 pb-3">
          <SheetTitle>{heading}</SheetTitle>
        </SheetHeader>
        {body}
      </SheetContent>
    </Sheet>
  );
}
