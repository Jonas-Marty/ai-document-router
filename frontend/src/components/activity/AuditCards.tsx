import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import type { AuditEvent } from "@/services/api/types";
import { AuditDetails, eventTokens } from "./AuditDetails";

/** SPEC 8.11's mobile stacked cards: the summary as the title, who and when underneath. */
export function AuditCards({ items }: { items: AuditEvent[] }) {
  return (
    <div className="space-y-3">
      {items.map((event) => (
        <Card key={event.id} size="sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium">{event.summary}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm text-muted-foreground">
            <p className="flex flex-wrap gap-x-2">
              <span className="min-w-0 truncate">{event.actor_label}</span>
              <span aria-hidden="true">·</span>
              <span title={absoluteTime(event.occurred_at)}>{relativeTime(event.occurred_at)}</span>
              {event.total_tokens !== null && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{eventTokens(event)} tokens</span>
                </>
              )}
            </p>
            <AuditDetails event={event} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
