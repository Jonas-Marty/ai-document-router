import { formatCost, formatTokens } from "@/lib/tokens";
import type { StageUsage, UsageSummary } from "@/services/api/types";

const STAGES = [
  ["extraction", "Extraction"],
  ["filing", "Filing"],
] as const;

/** SPEC 8.3: what the latest analysis of this document cost, in tokens. One muted line,
 * expandable to the prompt/completion split per task. The cost joins in when a model had a
 * price, a minimum on the same terms as the tokens. A `<details>` rather than a tooltip, so
 * it opens with a tap on a phone. Renders nothing when no model was reached. */
export function TokenUsage({ usage }: { usage: UsageSummary | null }) {
  if (!usage) return null;
  const stages = STAGES.flatMap(([key, label]) => {
    const stage = usage[key];
    return stage ? [{ key, label, stage }] : [];
  });
  const unreported = stages.reduce((sum, { stage }) => sum + stage.unreported_calls, 0);
  const atLeast = unreported > 0 ? "≥ " : "";

  return (
    <details className="group text-xs text-muted-foreground">
      <summary className="cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
        <span className="font-medium">Tokens</span>
        {stages.map(({ key, label, stage }) => (
          <span key={key}>
            {" · "}
            {label.toLowerCase()} {formatTokens(stage.total_tokens)}
          </span>
        ))}
        <span>
          {" · total "}
          {atLeast}
          {formatTokens(usage.total_tokens)}
        </span>
        {usage.cost !== null && (
          <span>
            {" · cost "}
            {atLeast}
            {formatCost(usage.cost)}
          </span>
        )}
      </summary>
      <div className="mt-1.5 space-y-1.5 rounded-lg border border-border bg-muted/30 p-2">
        <dl className="space-y-1.5">
          {stages.map(({ key, label, stage }) => (
            <StageRow key={key} label={label} stage={stage} />
          ))}
        </dl>
        {unreported > 0 && (
          <p>
            {unreported === 1 ? "1 call" : `${unreported} calls`} didn't report usage, so the totals
            are a minimum.
          </p>
        )}
      </div>
    </details>
  );
}

function StageRow({ label, stage }: { label: string; stage: StageUsage }) {
  return (
    <div>
      <dt className="font-medium text-foreground">
        {label} <span className="font-normal text-muted-foreground">· {stage.model_label}</span>
      </dt>
      <dd className="tabular-nums">
        {stage.prompt_tokens.toLocaleString("en")} prompt +{" "}
        {stage.completion_tokens.toLocaleString("en")} completion ={" "}
        {stage.total_tokens.toLocaleString("en")}
        {stage.calls > 1 && ` · ${stage.calls} calls`}
        {stage.cost !== null && ` · cost ${formatCost(stage.cost)}`}
      </dd>
    </div>
  );
}
