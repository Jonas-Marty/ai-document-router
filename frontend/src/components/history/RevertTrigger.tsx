import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const NOT_REVERTIBLE_REASON = "Already reverted, or the file has moved.";

/** SPEC 8.6: "Non-revertible rows show a disabled button with tooltip." Deliberately not the
 * native `disabled` attribute -- that sets `pointer-events: none` (see button.tsx's own
 * `disabled:pointer-events-none`), which stops a Radix Tooltip's hover/focus trigger from
 * ever firing on the element it's attached to. `aria-disabled` plus a guarded `onClick` gets
 * the same look and the same "can't actually revert" behaviour while leaving the element
 * hoverable/focusable so the tooltip still opens. */
export function RevertTrigger({
  revertible,
  onClick,
}: {
  revertible: boolean;
  onClick: () => void;
}) {
  const button = (
    <Button
      variant="outline"
      size="sm"
      aria-disabled={!revertible}
      className={cn(!revertible && "cursor-not-allowed opacity-50")}
      onClick={() => revertible && onClick()}
    >
      Revert
    </Button>
  );

  if (revertible) return button;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{NOT_REVERTIBLE_REASON}</TooltipContent>
    </Tooltip>
  );
}
