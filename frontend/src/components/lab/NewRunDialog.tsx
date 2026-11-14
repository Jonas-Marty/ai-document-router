import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
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
import { useStartLabRun, useTestSets } from "@/hooks/useLab";
import { ApiError } from "@/services/api/errors";

export const MAX_RUN_CONFIGURATIONS = 6;

/** SPEC 8.13a: a test set and one to six configurations, the default preselected. Mounted
 * only while open, so each opening starts from the default again. */
export function NewRunDialog({
  open,
  onOpenChange,
  testSetId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  testSetId?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && <NewRunForm testSetId={testSetId} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function NewRunForm({ testSetId, onClose }: { testSetId?: string; onClose: () => void }) {
  const navigate = useNavigate();
  const testSets = useTestSets();
  const configurations = useConfigurations();
  const start = useStartLabRun();
  const [chosenSet, setChosenSet] = useState<string | null>(testSetId ?? null);
  const [picked, setPicked] = useState<string[] | null>(null);

  const usableSets = (testSets.data ?? []).filter((set) => set.case_count > 0);
  const setId = chosenSet ?? usableSets[0]?.id ?? null;
  const defaultId = configurations.data?.find((row) => row.is_default)?.id;
  // Until someone ticks a box, the default is the choice.
  const chosen = picked ?? (defaultId ? [defaultId] : []);
  const full = chosen.length >= MAX_RUN_CONFIGURATIONS;

  function toggle(id: string) {
    setPicked(chosen.includes(id) ? chosen.filter((row) => row !== id) : [...chosen, id]);
  }

  function submit() {
    if (!setId || chosen.length === 0) return;
    start.mutate(
      { test_set_id: setId, configuration_ids: chosen },
      {
        onSuccess: (run) => {
          onClose();
          navigate(`/lab/runs/${run.id}`);
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : "Couldn't start the run."),
      },
    );
  }

  const noSets = testSets.isSuccess && usableSets.length === 0;

  return (
    <>
      <DialogHeader>
        <DialogTitle>New run</DialogTitle>
        <DialogDescription>
          Every document of the set is filed with each configuration and scored against the answer
          it was filed with.
        </DialogDescription>
      </DialogHeader>
      {noSets ? (
        <p className="text-sm">
          No test set has documents yet.{" "}
          <Link to="/lab/test-sets" className="underline underline-offset-4" onClick={onClose}>
            Make one
          </Link>{" "}
          first.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="run-test-set">Test set</Label>
            <Select value={setId ?? undefined} onValueChange={setChosenSet}>
              <SelectTrigger id="run-test-set" className="w-full">
                <SelectValue placeholder="Choose a test set" />
              </SelectTrigger>
              <SelectContent>
                {usableSets.map((set) => (
                  <SelectItem key={set.id} value={set.id}>
                    {set.name} ({set.case_count})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Configurations</legend>
            <p className="text-xs text-muted-foreground">
              Up to {MAX_RUN_CONFIGURATIONS}. Each one costs a full analysis per document.
            </p>
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {(configurations.data ?? []).map((configuration) => {
                const checked = chosen.includes(configuration.id);
                return (
                  <li key={configuration.id}>
                    <label className="flex min-h-9 items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4 shrink-0 accent-primary"
                        checked={checked}
                        disabled={!checked && full}
                        onChange={() => toggle(configuration.id)}
                      />
                      <span className="min-w-0 break-words">{configuration.name}</span>
                      {configuration.is_default && (
                        <span className="text-xs text-muted-foreground">default</span>
                      )}
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
        </div>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="button"
          disabled={!setId || chosen.length === 0 || start.isPending}
          onClick={submit}
        >
          {start.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
          Start run
        </Button>
      </DialogFooter>
    </>
  );
}
