import { formatTokens } from "@/lib/tokens";
import type { AuditUsage } from "@/services/api/types";

/** SPEC 8.11: what the models have been asked to do lately. Plain stat tiles and a list, not
 * a chart: a handful of headline numbers is read faster as figures than as bars. Compact
 * figures in the tiles, the exact count on hover. */
export function UsageSummary({ usage }: { usage: AuditUsage }) {
  const idle = usage.analyses === 0 && usage.comparisons === 0 && usage.total_tokens === 0;

  return (
    <section aria-labelledby="usage-heading" className="space-y-3">
      <h2 id="usage-heading" className="text-sm font-medium text-muted-foreground">
        AI usage · last {usage.days} days
      </h2>
      {idle ? (
        <p className="text-sm text-muted-foreground">No documents were analysed in this period.</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Tile label="Analyses" value={usage.analyses.toLocaleString("en")} />
            <Tile label="Comparisons" value={usage.comparisons.toLocaleString("en")} />
            <Tile
              label="Tokens"
              value={formatTokens(usage.total_tokens)}
              exact={usage.total_tokens}
            />
            <Tile
              label="Prompt · completion"
              value={`${formatTokens(usage.prompt_tokens)} · ${formatTokens(usage.completion_tokens)}`}
              exact={`${usage.prompt_tokens.toLocaleString("en")} prompt, ${usage.completion_tokens.toLocaleString("en")} completion`}
            />
          </dl>
          {usage.by_model.length > 0 && (
            <ul
              aria-label="Tokens by model"
              className="divide-y divide-border rounded-lg border border-border text-sm"
            >
              {usage.by_model.map((model) => (
                <li
                  key={`${model.task}-${model.model_label}`}
                  className="flex flex-wrap items-baseline justify-between gap-x-3 px-3 py-2"
                >
                  <span className="min-w-0">
                    <span className="capitalize text-muted-foreground">{model.task}</span>
                    {" · "}
                    <span className="break-words">{model.model_label}</span>
                  </span>
                  <span
                    className="text-muted-foreground tabular-nums"
                    title={`${model.prompt_tokens.toLocaleString("en")} prompt + ${model.completion_tokens.toLocaleString("en")} completion`}
                  >
                    {formatTokens(model.total_tokens)} tokens ·{" "}
                    {model.calls === 1 ? "1 call" : `${model.calls.toLocaleString("en")} calls`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function Tile({ label, value, exact }: { label: string; value: string; exact?: number | string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className="mt-1 text-xl font-semibold"
        title={typeof exact === "number" ? exact.toLocaleString("en") : exact}
      >
        {value}
      </dd>
    </div>
  );
}
