import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { AIProposal } from "@/services/api/types";

/** SPEC 8.3.5a: lets someone see exactly what was sent to the AI for this proposal --
 * the fixed instructions plus the per-document folder tree, sample filenames, and text --
 * so they can judge a bad proposal and tune Settings (naming pattern, allowed folders)
 * instead of guessing. Collapsed by default: most reviews never need it. */
export function PromptDetails({ proposal }: { proposal: AIProposal }) {
  const [expanded, setExpanded] = useState(false);

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
        {expanded ? "Hide what was sent to the AI" : "Show what was sent to the AI"}
      </Button>
      {expanded && (
        <div className="space-y-3">
          <PromptBlock label="Instructions" text={proposal.system_prompt} />
          {proposal.prompt_text === null ? (
            <p className="text-sm text-muted-foreground">
              The document-specific part wasn't recorded for this proposal. Regenerate it to see
              this.
            </p>
          ) : (
            <PromptBlock label="Sent with this document" text={proposal.prompt_text} />
          )}
        </div>
      )}
    </div>
  );
}

function PromptBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs text-muted-foreground">
        {text}
      </pre>
    </div>
  );
}
