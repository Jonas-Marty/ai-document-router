import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// SPEC 7.4.
const HIGH_THRESHOLD = 0.85;
const MEDIUM_THRESHOLD = 0.6;

function level(score: number): { label: string; className: string } {
  if (score >= HIGH_THRESHOLD) {
    return {
      label: "High",
      className:
        "border-green-600/30 bg-green-600/10 text-green-700 dark:border-green-400/30 dark:text-green-400",
    };
  }
  if (score >= MEDIUM_THRESHOLD) {
    return {
      label: "Medium",
      className:
        "border-amber-600/30 bg-amber-600/10 text-amber-700 dark:border-amber-400/30 dark:text-amber-400",
    };
  }
  return {
    label: "Low",
    className:
      "border-red-600/30 bg-red-600/10 text-red-700 dark:border-red-400/30 dark:text-red-400",
  };
}

/** SPEC 7.4: confidence never changes app behaviour, so this is display-only -- no
 * auto-approval, no disabling anything, just the badge (and, below 0.60, a banner rendered
 * by the caller). */
export function ConfidenceBadge({ score }: { score: number }) {
  const { label, className } = level(score);
  return (
    <Badge variant="outline" className={cn(className)}>
      {label} confidence · {Math.round(score * 100)}%
    </Badge>
  );
}

export function isLowConfidence(score: number): boolean {
  return score < MEDIUM_THRESHOLD;
}
