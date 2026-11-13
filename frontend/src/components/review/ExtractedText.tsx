import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { Document } from "@/services/api/types";
import { PromptBlock } from "./PromptDetails";

/** The reading the latest analysis filed from -- the text the filing step was actually
 * given. Lets someone tell a misread scan from a bad filing decision. Collapsed by default,
 * like PromptDetails: it is long, and most reviews never need it. */
export function ExtractedText({ document }: { document: Document }) {
  const [expanded, setExpanded] = useState(false);
  const { reading } = document;
  if (!reading) return null;

  return (
    <div className="space-y-1.5">
      <Button
        type="button"
        variant="link"
        size="sm"
        className="h-auto p-0 text-muted-foreground"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? "Hide the text filing read" : "Show the text filing read"}
      </Button>
      {expanded && <PromptBlock label={`Read by ${reading.label}`} text={reading.text} />}
    </div>
  );
}
