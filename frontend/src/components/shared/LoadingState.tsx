import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** SPEC 8.10: every data-backed surface has an explicit loading state. This is the generic
 * one -- a centered spinner with an optional label -- used until a surface earns a more
 * specific skeleton in a later milestone. */
export function LoadingState({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground",
        className,
      )}
    >
      <Loader2 className="size-5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      <span className="text-sm">{label}</span>
    </div>
  );
}
