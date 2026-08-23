import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** SPEC 8.10: "error card + Try again" is the pattern for every data-backed surface.
 * `message` is shown verbatim -- for API failures that's the backend's own message
 * (SPEC 5), written for a human. */
export function ErrorState({
  message = "Something went wrong.",
  onRetry,
  className,
}: {
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-border p-8 text-center",
        className,
      )}
    >
      <AlertCircle className="size-6 text-destructive" aria-hidden="true" />
      <p className="text-sm text-muted-foreground">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
