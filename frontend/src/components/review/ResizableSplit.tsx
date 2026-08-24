import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import { cn } from "@/lib/utils";

/** SPEC 8.3: "Resizable split, min 30% per side, ratio persisted to localStorage", default
 * 55/45. react-resizable-panels 4.x renamed its components from the older
 * PanelGroup/Panel/PanelResizeHandle API (which most tutorials and shadcn's own `resizable`
 * component still assume) to Group/Panel/Separator with a `useDefaultLayout` hook for
 * persistence -- checked against the installed version's actual .d.ts rather than assumed,
 * since the two APIs aren't drop-in compatible. */
export function ResizableSplit({ left, right }: { left: React.ReactNode; right: React.ReactNode }) {
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: "review-split",
    storage: window.localStorage,
  });

  return (
    <Group
      id="review-split"
      orientation="horizontal"
      defaultLayout={defaultLayout}
      onLayoutChanged={onLayoutChanged}
      className="flex h-full w-full"
    >
      <Panel id="viewer" defaultSize="55" minSize="30" className="min-w-0">
        {left}
      </Panel>
      <Separator className="group relative w-px shrink-0 cursor-col-resize bg-border outline-none focus-visible:bg-ring">
        <span
          className={cn(
            "absolute inset-y-0 left-1/2 w-3 -translate-x-1/2",
            "group-hover:bg-accent/50 group-focus-visible:bg-accent/50",
          )}
        />
      </Separator>
      <Panel id="form" defaultSize="45" minSize="30" className="min-w-0 overflow-y-auto">
        {right}
      </Panel>
    </Group>
  );
}
