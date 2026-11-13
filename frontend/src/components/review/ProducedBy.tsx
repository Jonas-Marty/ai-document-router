import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Document } from "@/services/api/types";
import { PromptDetails } from "./PromptDetails";
import { ReasoningBlock } from "./ReasoningBlock";
import { TokenUsage } from "./TokenUsage";

export interface ProducedByProps {
  document: Document;
  isReanalysing: boolean;
  onReanalyse: () => void;
  onReanalyseWith: () => void;
  onCompare: () => void;
}

/** SPEC 8.3 item 5: where the proposal came from, and the way to ask for another one. The
 * reasoning, the prompt and the tokens sit inside it because they answer the same question
 * -- why does the proposal look like this -- and are read together when it is wrong. */
export function ProducedBy({
  document,
  isReanalysing,
  onReanalyse,
  onReanalyseWith,
  onCompare,
}: ProducedByProps) {
  const proposal = document.proposal;
  const failed = document.proposal_status === "failed";
  const facts = proposal
    ? [
        proposal.configuration_name && `Configuration “${proposal.configuration_name}”`,
        proposal.reading_label && `read with ${proposal.reading_label}`,
      ].filter(Boolean)
    : [];
  const model = proposal
    ? proposal.connection_name
      ? `${proposal.connection_name} · ${proposal.model_name}`
      : proposal.model_name
    : null;

  return (
    <section
      aria-labelledby="produced-by"
      className="space-y-3 rounded-lg border border-border p-3"
    >
      <div className="space-y-1">
        <h2 id="produced-by" className="text-sm font-medium">
          Produced by
        </h2>
        {proposal ? (
          <>
            {facts.length > 0 && (
              <p className="text-sm text-muted-foreground">{facts.join(" · ")}</p>
            )}
            {model && (
              <p title="Connection · model" className="font-mono text-xs break-all">
                {model}
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            {failed ? "No proposal: the analysis failed." : "No proposal yet."}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={isReanalysing}
          onClick={onReanalyse}
        >
          {isReanalysing ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <RotateCcw aria-hidden="true" />
          )}
          {/* SPEC 8.8: a failed proposal is retried with the same request, under the name a
              failure asks for. */}
          {failed ? "Try again" : "Re-analyse"}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={isReanalysing}
          onClick={onReanalyseWith}
        >
          Re-analyse with…
        </Button>
        <Button type="button" variant="link" size="sm" className="px-1" onClick={onCompare}>
          Compare methods
        </Button>
      </div>

      {proposal && <ReasoningBlock text={proposal.reasoning_text} />}
      {proposal && <PromptDetails proposal={proposal} />}
      {/* Outside the proposal check: a failed analysis spent tokens too. */}
      <TokenUsage usage={document.analysis_usage} />
    </section>
  );
}
