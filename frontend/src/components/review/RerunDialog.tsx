import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ModelPicker, optionsWithCurrent } from "@/components/settings/ModelPicker";
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
import { useAiEndpoints, useAiTasks } from "@/hooks/useAiConfig";
import { useModelsByEndpoint } from "@/hooks/useModelsByEndpoint";
import type { RegenerateChoice } from "@/services/api/types";

export interface RerunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isPending: boolean;
  onRun: (choice: RegenerateChoice) => void;
}

/** Pick the connection and model for one re-run of the analysis.
 *
 * Starts on the first step of the configured filing chain, so confirming without touching
 * anything repeats what the poller would have done. A pick is for this run only and does not
 * fall back to the chain: the point of choosing is to find out what *that* model says. */
export function RerunDialog({ open, onOpenChange, isPending, onRun }: RerunDialogProps) {
  const endpoints = useAiEndpoints();
  const tasks = useAiTasks();
  const models = useModelsByEndpoint();
  const [endpointId, setEndpointId] = useState("");
  const [modelName, setModelName] = useState("");
  const [manual, setManual] = useState(false);

  const firstStep = tasks.data?.find((chain) => chain.task === "filing")?.steps[0];
  const defaultEndpoint = firstStep?.endpoint_id ?? endpoints.data?.[0]?.id ?? "";
  const defaultModel = firstStep?.model_name ?? "";

  useEffect(() => {
    if (!open) return;
    setEndpointId(defaultEndpoint);
    setModelName(defaultModel);
    setManual(false);
  }, [open, defaultEndpoint, defaultModel]);

  const endpoint = endpoints.data?.find((candidate) => candidate.id === endpointId);
  const canRun = endpoint !== undefined && modelName.trim() !== "" && !isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Re-run analysis</DialogTitle>
          <DialogDescription>
            Choose which connection and model should propose the name and folder this time. Only the
            chosen model is asked — there is no fallback to the others.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="rerun-endpoint">Connection</Label>
            <Select
              value={endpointId}
              onValueChange={(value) => {
                setEndpointId(value);
                setModelName(value === defaultEndpoint ? defaultModel : "");
              }}
            >
              <SelectTrigger id="rerun-endpoint" className="w-full">
                <SelectValue placeholder="Choose a connection" />
              </SelectTrigger>
              <SelectContent>
                {(endpoints.data ?? []).map((candidate) => (
                  <SelectItem key={candidate.id} value={candidate.id}>
                    {candidate.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {endpoints.data?.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No connections yet — add one in Settings first.
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="rerun-model">Model</Label>
            <ModelPicker
              id="rerun-model"
              value={modelName}
              onChange={setModelName}
              options={optionsWithCurrent(
                endpoint ? models.byEndpoint[endpoint.id] : undefined,
                modelName,
              )}
              disabled={endpoint === undefined}
              isPending={endpoint !== undefined && models.loadingId === endpoint.id}
              errorMessage={endpoint ? (models.errors[endpoint.id] ?? null) : null}
              onOpen={() => endpoint && models.load(endpoint)}
              manualMode={manual}
              onToggleManual={() => setManual((current) => !current)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!canRun}
            onClick={() => onRun({ endpoint_id: endpointId, model_name: modelName.trim() })}
          >
            {isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
            Run analysis
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
