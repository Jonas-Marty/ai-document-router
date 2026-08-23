import type { ComponentType, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** SPEC 8.10 / 9: empty states invite an action rather than just stating there's nothing
 * there. `action` is optional -- some empty states (e.g. "No subfolders") have nothing
 * useful to offer. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ComponentType<{ className?: string }>;
  title: string;
  description?: ReactNode;
  action?: { label: string; onClick: () => void };
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-2 py-12 text-center", className)}
    >
      {Icon && <Icon className="mb-2 size-8 text-muted-foreground" />}
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && (
        <Button variant="outline" size="sm" className="mt-2" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
