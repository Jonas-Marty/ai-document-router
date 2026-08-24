import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const SHORTCUTS: [string, string][] = [
  ["⌘/Ctrl + Enter", "Approve & move"],
  ["S", "Skip for now"],
  ["F", "Open folder picker"],
  ["N", "Focus file name"],
  ["← / →", "Previous / next page"],
  ["?", "Show this cheat sheet"],
];

/** SPEC 8.9: "? opens a cheat sheet." Desktop only, same as the shortcuts it documents. */
export function ShortcutCheatSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
        </DialogHeader>
        <dl className="space-y-2">
          {SHORTCUTS.map(([key, action]) => (
            <div key={key} className="flex items-center justify-between gap-4 text-sm">
              <dt className="text-muted-foreground">{action}</dt>
              <dd className="rounded border border-border bg-muted px-2 py-0.5 font-mono">{key}</dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
