import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import { FolderPickerBody } from "./FolderPickerBody";

export interface FolderPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onSelect: (path: string) => void;
}

/** SPEC 8.5: "Desktop: dialog with arrow-key navigation and Enter to select. Mobile:
 * full-screen sheet, tap to select." Both breakpoints share `FolderPickerBody` for the
 * actual tree/filter/create-folder/footer -- only the surrounding chrome, and whether
 * keyboard navigation is wired up, differs here. */
export function FolderPicker({ open, onOpenChange, value, onSelect }: FolderPickerProps) {
  const isDesktop = useIsDesktop();

  function commit(path: string) {
    onSelect(path);
    onOpenChange(false);
  }

  function cancel() {
    onOpenChange(false);
  }

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex h-[32rem] max-w-lg flex-col gap-0 p-0">
          <DialogHeader className="p-4 pb-3">
            <DialogTitle>Choose folder</DialogTitle>
          </DialogHeader>
          <FolderPickerBody value={value} onSelect={commit} onCancel={cancel} enableKeyboardNav />
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
          <SheetTitle>Choose folder</SheetTitle>
        </SheetHeader>
        <FolderPickerBody
          value={value}
          onSelect={commit}
          onCancel={cancel}
          enableKeyboardNav={false}
        />
      </SheetContent>
    </Sheet>
  );
}
