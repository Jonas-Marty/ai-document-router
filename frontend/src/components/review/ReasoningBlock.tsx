import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** SPEC 8.3.5: "muted bordered block, whitespace-pre-wrap, clamped to 4 lines with 'Show
 * more'." reasoning_text is plain text (SPEC 4.2 -- "not markdown"), so no rendering beyond
 * preserving whitespace is needed. */
export function ReasoningBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
      <p className={cn("whitespace-pre-wrap", !expanded && "line-clamp-4")}>{text}</p>
      {!expanded && (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="mt-1 h-auto p-0"
          onClick={() => setExpanded(true)}
        >
          Show more
        </Button>
      )}
    </div>
  );
}
