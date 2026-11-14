import type { LabRun } from "@/services/api/types";
import { formatTimeLeft } from "./labFormat";

/** How far a run has got, and why it is not moving when it isn't. */
export function RunProgress({ run, showStage = false }: { run: LabRun; showStage?: boolean }) {
  const share = run.total ? run.finished / run.total : 0;
  const running = run.status === "running";
  const detail = running
    ? (run.waiting_reason ??
      (showStage ? run.stage : null) ??
      formatTimeLeft(run.remaining_seconds))
    : run.status === "cancelled"
      ? "Cancelled"
      : null;
  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-label={`${run.test_set_name} progress`}
        aria-valuemin={0}
        aria-valuemax={run.total}
        aria-valuenow={run.finished}
        className="h-1.5 overflow-hidden rounded-full bg-secondary"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none"
          style={{ width: `${Math.round(share * 100)}%` }}
        />
      </div>
      <p className="flex flex-wrap justify-between gap-x-3 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {run.finished} / {run.total}
        </span>
        {detail && <span aria-live="polite">{detail}</span>}
      </p>
    </div>
  );
}
