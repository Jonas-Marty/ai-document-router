import { cn } from "@/lib/utils";
import type { AIProposal } from "@/services/api/types";

function label(proposal: AIProposal): string {
  return proposal.connection_name
    ? `${proposal.connection_name} · ${proposal.model_name}`
    : proposal.model_name;
}

/** Which connection and model wrote the proposal on screen. Outlined when the person picked
 * it for this run; tagged when a fallback answered because the first choice was down. */
export function ModelChip({ proposal }: { proposal: AIProposal }) {
  return (
    <>
      <span
        title="Connection · model"
        className={cn(
          "rounded-md border bg-muted px-1.5 py-0.5 font-mono text-xs break-all",
          proposal.chosen_by_user && "border-primary bg-transparent text-primary",
        )}
      >
        {label(proposal)}
      </span>
      {proposal.fallback_from && (
        <span className="rounded-full border border-amber-600/30 px-2 py-0.5 text-xs text-amber-700 dark:border-amber-400/30 dark:text-amber-400">
          fallback
        </span>
      )}
    </>
  );
}

export function ModelHint({ proposal }: { proposal: AIProposal }) {
  if (proposal.chosen_by_user) {
    return (
      <p className="text-xs text-muted-foreground">Chosen by you for this run, no fallback.</p>
    );
  }
  if (proposal.fallback_from) {
    return <p className="text-xs text-muted-foreground">{proposal.fallback_from} didn't answer.</p>;
  }
  return null;
}
