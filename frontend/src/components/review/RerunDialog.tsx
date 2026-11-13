import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConfigurations } from "@/hooks/useAiConfig";
import type { RegenerateChoice } from "@/services/api/types";

export interface RerunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isPending: boolean;
  /** The configuration picked for this document, or null when it follows the default. */
  currentConfigurationName: string | null;
  onRun: (choice: RegenerateChoice) => void;
}

/** Pick the configuration to analyse this document with.
 *
 * Starts on the one the document uses now, so confirming without touching anything repeats
 * the analysis. The pick stays with the document (SPEC 6.3a); picking the default puts it
 * back to following the default. Nothing falls back to another configuration. */
export function RerunDialog({
  open,
  onOpenChange,
  isPending,
  currentConfigurationName,
  onRun,
}: RerunDialogProps) {
  const configurations = useConfigurations();
  const [configurationId, setConfigurationId] = useState("");
  const list = configurations.data ?? [];
  const current =
    list.find((row) => row.name === currentConfigurationName) ?? list.find((row) => row.is_default);
  const currentId = current?.id ?? "";

  useEffect(() => {
    if (open) setConfigurationId(currentId);
  }, [open, currentId]);

  const canRun = configurationId !== "" && !isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Re-analyse with…</DialogTitle>
          <DialogDescription>
            Choose the configuration that reads and files this document. It keeps that choice for
            later re-runs; nothing falls back to another configuration.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="rerun-configuration">Configuration</Label>
          <Select value={configurationId} onValueChange={setConfigurationId}>
            <SelectTrigger id="rerun-configuration" className="w-full">
              <SelectValue placeholder="Choose a configuration" />
            </SelectTrigger>
            <SelectContent>
              {list.map((row) => (
                <SelectItem key={row.id} value={row.id}>
                  {row.is_default ? `${row.name} (default)` : row.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {configurations.isSuccess && list.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No configurations yet —{" "}
              <Link to="/lab" className="underline underline-offset-4">
                create one in the Lab
              </Link>
              .
            </p>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!canRun}
            onClick={() => onRun({ configuration_id: configurationId })}
          >
            {isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
            Run analysis
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
