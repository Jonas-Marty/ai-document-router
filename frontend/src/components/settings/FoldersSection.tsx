import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, X } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUpdateSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import { type FoldersFormValues, foldersFormSchema } from "./settingsSchemas";

/** SPEC 8.7's Folders section: the allowed roots list and the trash folder. `PUT /settings`
 * is whole-object (SettingsUpdate requires every field), so a save here has to carry the
 * *other* sections' last-saved values along too -- `settings` (the live query data, always
 * the latest known-good server state) supplies those, spread first so this section's own
 * fields override them. */
export function FoldersSection({
  settings,
  onDirtyChange,
}: {
  settings: Settings;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const updateSettings = useUpdateSettings();
  // `values` must stay reference-stable across incidental re-renders (typing in a sibling
  // field, an onDirtyChange callback firing) -- a fresh object/array here on every render
  // churns useFieldArray's ids and can remount the row inputs mid-interaction, dropping
  // focus. `settings` itself is only a new reference when the query actually refetches.
  const values = useMemo(
    () => ({
      allowed_root_folders: settings.allowed_root_folders.map((value) => ({ value })),
      trash_folder_path: settings.trash_folder_path,
    }),
    [settings],
  );
  // `resetOptions.keepDirtyValues` protects an in-progress edit here from being wiped by the
  // *reactive* values-sync (e.g. another section's save touches the same whole-object PUT).
  // It's also the default for any *explicit* `reset()` call unless overridden -- which would
  // otherwise make Discard/post-save resets below silently no-op on whichever row is dirty,
  // since "dirty" is exactly the row being discarded/just-saved. Both explicit resets pass
  // `{ keepDirtyValues: false }` to force a real, unconditional reset.
  const methods = useForm<FoldersFormValues>({
    resolver: zodResolver(foldersFormSchema),
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const { fields, append, remove } = useFieldArray({
    control: methods.control,
    name: "allowed_root_folders",
  });
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
  } = methods;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(values: FoldersFormValues) {
    updateSettings.mutate(
      {
        ...settings,
        allowed_root_folders: values.allowed_root_folders.map((r) => r.value.trim()),
        trash_folder_path: values.trash_folder_path.trim(),
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              allowed_root_folders: saved.allowed_root_folders.map((value) => ({ value })),
              trash_folder_path: saved.trash_folder_path,
            },
            { keepDirtyValues: false },
          );
          toast.success("Folders saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save folders.");
        },
      },
    );
  }

  const rootFoldersError = errors.allowed_root_folders?.root?.message;

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="Folders"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <div className="space-y-2">
          <Label>Allowed root folders</Label>
          <div className="space-y-2">
            {fields.map((field, index) => (
              <div key={field.id}>
                <div className="flex items-center gap-2">
                  <Input
                    className="font-mono"
                    aria-label={`Allowed root folder ${index + 1}`}
                    aria-invalid={!!errors.allowed_root_folders?.[index]?.value}
                    {...register(`allowed_root_folders.${index}.value`)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => remove(index)}
                    disabled={fields.length <= 1}
                    aria-label="Remove folder"
                  >
                    <X aria-hidden="true" />
                  </Button>
                </div>
                {errors.allowed_root_folders?.[index]?.value && (
                  <p className="mt-1 text-sm text-destructive">
                    {errors.allowed_root_folders[index]?.value?.message}
                  </p>
                )}
              </div>
            ))}
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => append({ value: "" })}>
            <Plus aria-hidden="true" />
            Add folder
          </Button>
          {rootFoldersError && <p className="text-sm text-destructive">{rootFoldersError}</p>}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="trash_folder_path">Trash folder</Label>
          <Input
            id="trash_folder_path"
            className="font-mono"
            aria-invalid={!!errors.trash_folder_path}
            {...register("trash_folder_path")}
          />
          {errors.trash_folder_path && (
            <p className="text-sm text-destructive">{errors.trash_folder_path.message}</p>
          )}
        </div>
      </SectionCard>
    </form>
  );
}
