import { formatTokens } from "@/lib/tokens";
import type { AuditEvent } from "@/services/api/types";

/** SPEC 8.11: everything an event recorded beyond its one-line summary, one tap away. The
 * `details` object differs per action and is only typed as `unknown` on this side, so values
 * are rendered by shape rather than by key: a new field on the backend shows up here without
 * a frontend change. A `<details>` rather than row state, so it works the same in the desktop
 * table and the mobile cards. */
export function AuditDetails({ event }: { event: AuditEvent }) {
  const entries: [string, unknown][] = [
    ["action", event.action],
    ...(event.document_id ? ([["document", event.document_id]] as [string, unknown][]) : []),
    ...(event.total_tokens !== null
      ? ([
          [
            "tokens",
            `${(event.prompt_tokens ?? 0).toLocaleString("en")} prompt + ${(
              event.completion_tokens ?? 0
            ).toLocaleString("en")} completion = ${event.total_tokens.toLocaleString("en")}`,
          ],
        ] as [string, unknown][])
      : []),
    ...Object.entries(event.details),
  ];

  return (
    <details className="text-xs text-muted-foreground">
      <summary className="w-fit cursor-pointer select-none hover:text-foreground">Details</summary>
      <dl className="mt-1.5 grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-lg border border-border bg-muted/30 p-2">
        {entries.map(([key, value]) => (
          <DetailRow key={key} label={key.replaceAll("_", " ")} value={value} />
        ))}
      </dl>
    </details>
  );
}

function DetailRow({ label, value }: { label: string; value: unknown }) {
  const text = formatValue(value);
  return (
    <>
      <dt className="font-medium text-foreground">{label}</dt>
      <dd className="min-w-0">
        {text === null ? (
          <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono">
            {JSON.stringify(value, null, 2)}
          </pre>
        ) : (
          <span className="break-all font-mono">{text}</span>
        )}
      </dd>
    </>
  );
}

/** A one-line rendering, or null when the value is structured enough to need the JSON view. */
function formatValue(value: unknown): string | null {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString("en");
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item !== "object" || item === null)) {
    return value.length === 0 ? "none" : value.map((item) => String(item)).join(", ");
  }
  return null;
}

/** "4.6K" for the tokens column, or a dash for events that didn't call a model. */
export function eventTokens(event: AuditEvent): string {
  return event.total_tokens === null ? "—" : formatTokens(event.total_tokens);
}
