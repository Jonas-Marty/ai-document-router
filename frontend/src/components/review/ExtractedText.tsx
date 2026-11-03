import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { Document } from "@/services/api/types";
import { PromptBlock } from "./PromptDetails";

/** What the extraction task read off the pages -- the text filing was actually given. Lets
 * someone tell a misread scan from a bad filing decision. Collapsed by default, like
 * PromptDetails: it is long, and most reviews never need it. */
export function ExtractedText({ document }: { document: Document }) {
  const [expanded, setExpanded] = useState(false);
  const {
    extracted_markdown: markdown,
    extraction_model: model,
    extraction_error: error,
  } = document;
  if (!markdown && !error) return null;

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
        {expanded ? "Hide extracted text" : "Show extracted text"}
      </Button>
      {expanded && (
        <div className="space-y-2">
          {error && (
            <p className="text-sm text-destructive">
              Extraction failed, so filing used the text layer instead: {error}
            </p>
          )}
          {markdown && <PromptBlock label={model ? `Read by ${model}` : "Read"} text={markdown} />}
        </div>
      )}
    </div>
  );
}
