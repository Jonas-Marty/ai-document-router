import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUpdateSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import { type AnalysisFormValues, analysisFormSchema } from "./settingsSchemas";

/** How the poller paces analyses. What each analysis sends lives on its configuration, in the
 * Lab. Same dirty/save/discard shape as NamingSection -- see FoldersSection for why `values`
 * is memoised and why the explicit resets pass `keepDirtyValues: false`. */
export function AnalysisSection({
  settings,
  onDirtyChange,
}: {
  settings: Settings;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const updateSettings = useUpdateSettings();
  const values = useMemo(() => toFormValues(settings), [settings]);
  const methods = useForm<AnalysisFormValues>({
    resolver: zodResolver(analysisFormSchema),
    mode: "onChange",
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
  } = methods;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(form: AnalysisFormValues) {
    updateSettings.mutate(
      { ...settings, proposal_batch_size: Number(form.proposal_batch_size) },
      {
        onSuccess: (saved) => {
          methods.reset(toFormValues(saved), { keepDirtyValues: false });
          toast.success("Analysis settings saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save the settings.");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="Analysis"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <p className="text-sm text-muted-foreground">
          Models, prompts and instructions are set per configuration in the{" "}
          <Link to="/lab/configurations" className="underline underline-offset-4">
            Lab
          </Link>
          .
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="proposal_batch_size">Documents per batch</Label>
          <Input
            id="proposal_batch_size"
            type="number"
            inputMode="numeric"
            className="w-32"
            aria-invalid={!!errors.proposal_batch_size}
            aria-describedby="proposal_batch_size-help"
            {...register("proposal_batch_size")}
          />
          {errors.proposal_batch_size ? (
            <p className="text-sm text-destructive">{errors.proposal_batch_size.message}</p>
          ) : (
            <p id="proposal_batch_size-help" className="text-sm text-muted-foreground">
              How many waiting documents are read before any of them is filed. When reading and
              filing run on one GPU, each batch loads each model once, so larger batches switch less
              but take longer before the first proposal appears.
            </p>
          )}
        </div>
      </SectionCard>
    </form>
  );
}

function toFormValues(settings: Settings): AnalysisFormValues {
  return { proposal_batch_size: String(settings.proposal_batch_size) };
}
