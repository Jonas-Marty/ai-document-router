import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

/** SPEC 8.7: "One form per section, save disabled until dirty, Discard changes alongside."
 * The enclosing `<form>` (with its own `handleSubmit`) lives in each section component, not
 * here -- this is just the shared shell so Save/Discard/dirty-gating look identical across
 * Folders, Naming, and AI. */
export function SectionCard({
  title,
  children,
  isDirty,
  isSaving,
  onDiscard,
}: {
  title: string;
  children: ReactNode;
  isDirty: boolean;
  isSaving: boolean;
  onDiscard: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
      <CardFooter className="justify-end gap-2">
        <Button type="button" variant="outline" disabled={!isDirty || isSaving} onClick={onDiscard}>
          Discard changes
        </Button>
        <Button type="submit" disabled={!isDirty || isSaving}>
          {isSaving && <Loader2 className="animate-spin" />}
          Save
        </Button>
      </CardFooter>
    </Card>
  );
}
