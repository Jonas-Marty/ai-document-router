import type { AIProposal } from "@/services/api/types";

function label(proposal: AIProposal): string {
  return proposal.connection_name
    ? `${proposal.connection_name} · ${proposal.model_name}`
    : proposal.model_name;
}

/** Which connection and model wrote the proposal on screen. */
export function ModelChip({ proposal }: { proposal: AIProposal }) {
  return (
    <span
      title="Connection · model"
      className="rounded-md border bg-muted px-1.5 py-0.5 font-mono text-xs break-all"
    >
      {label(proposal)}
    </span>
  );
}

/** Which configuration produced it and what it read. Absent on proposals from before
 * configurations, which recorded neither. */
export function ModelHint({ proposal }: { proposal: AIProposal }) {
  const parts = [
    proposal.configuration_name && `Configuration “${proposal.configuration_name}”`,
    proposal.reading_label && `read with ${proposal.reading_label}`,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <p className="text-xs text-muted-foreground">{parts.join(" · ")}</p>;
}
