import { AlertTriangle, Clock, FileText, Loader2, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { apiClient } from "@/services/api/client";
import type { CheckFlag, InboxCard, InboxGroup } from "@/services/api/types";

/** SPEC 6.4b: what each "needs a check" flag means, in the words a badge has room for. */
export const FLAG_LABELS: Record<CheckFlag, string> = {
  new_sender: "New sender",
  new_account: "New account",
  near_match_account: "Account almost matches",
  missing_sender: "No sender",
  missing_title: "No title",
  missing_date: "No date",
  unusual_folder: "Unusual folder",
  name_taken: "Name taken",
};

/** The first page, small. A document with no thumbnail (an image the server can't render,
 * a file gone from the Nextcloud) shows a plain icon instead of a broken image. */
export function Thumbnail({ id, className }: { id: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={cn(
        "flex aspect-[3/4] shrink-0 items-center justify-center overflow-hidden rounded border border-border bg-muted",
        className ?? "w-20 sm:w-24",
      )}
    >
      {failed ? (
        <FileText className="size-1/2 max-h-8 max-w-8 text-muted-foreground" aria-hidden="true" />
      ) : (
        <img
          src={apiClient.getThumbnailUrl(id)}
          alt=""
          loading="lazy"
          className="size-full object-cover object-top"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

function AnalysisStatus({ card }: { card: InboxCard }) {
  if (card.proposal_status === "failed") {
    return (
      <p className="flex items-start gap-1 text-sm text-destructive">
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span className="break-words">{card.error ?? "The analysis failed."}</span>
      </p>
    );
  }
  if (card.analysis_stage) {
    return (
      <p className="flex items-start gap-1 text-sm text-muted-foreground">
        <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin" aria-hidden="true" />
        <span className="break-words">{card.analysis_stage}</span>
      </p>
    );
  }
  return (
    <p className="flex items-start gap-1 text-sm text-muted-foreground">
      <Clock className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <span className="break-words">{card.waiting_reason ?? "Waiting for its turn"}</span>
    </p>
  );
}

/** SPEC 8.6: one document in the grid. Open groups link to the review screen and can be
 * selected; Done and Trashed show where the file went and offer revert. */
export function InboxCardView({
  card,
  group,
  selected,
  onSelectedChange,
  onRevert,
}: {
  card: InboxCard;
  group: InboxGroup;
  selected: boolean;
  onSelectedChange?: (selected: boolean) => void;
  onRevert?: () => void;
}) {
  const open = group === "analyse" || group === "ready";
  const title = card.name ?? card.original_filename;
  const titleId = `card-title-${card.id}`;

  return (
    <li
      aria-labelledby={titleId}
      className={cn(
        "relative flex gap-3 rounded-lg border border-border bg-card p-3 text-card-foreground",
        selected && "border-primary ring-1 ring-primary",
      )}
    >
      {onSelectedChange && (
        <input
          type="checkbox"
          className="absolute top-2 left-2 z-10 size-5 accent-primary"
          aria-label={`Select ${title}`}
          checked={selected}
          onChange={(event) => onSelectedChange(event.target.checked)}
        />
      )}
      <Thumbnail id={card.id} />
      <div className="min-w-0 flex-1 space-y-1">
        {open ? (
          <Link
            id={titleId}
            to={`/review/${card.id}`}
            className="block font-medium break-words hover:underline focus-visible:underline"
          >
            {title}
          </Link>
        ) : (
          <p id={titleId} className="font-medium break-words">
            {title}
          </p>
        )}
        {card.name && card.name !== card.original_filename && (
          <p className="text-xs break-all text-muted-foreground">{card.original_filename}</p>
        )}
        {card.folder && (
          <p className="font-mono text-xs break-all text-muted-foreground">{card.folder}</p>
        )}
        {group === "analyse" && <AnalysisStatus card={card} />}
        {(card.later || card.flags.length > 0) && (
          <ul className="flex flex-wrap gap-1" aria-label="Needs a check">
            {card.later && (
              <li>
                <Badge variant="outline">Later</Badge>
              </li>
            )}
            {card.flags.map((flag) => (
              <li key={flag}>
                <Badge
                  variant="secondary"
                  className="bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
                >
                  {FLAG_LABELS[flag]}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">
          {card.configuration_name && <span>{card.configuration_name} · </span>}
          <time dateTime={card.scanned_at} title={absoluteTime(card.scanned_at)}>
            {relativeTime(card.scanned_at)}
          </time>
        </p>
        {onRevert && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!card.revertible}
            title={card.revertible ? undefined : "Already reverted, or the file has moved."}
            onClick={onRevert}
          >
            <Undo2 aria-hidden="true" />
            Revert
          </Button>
        )}
      </div>
    </li>
  );
}
