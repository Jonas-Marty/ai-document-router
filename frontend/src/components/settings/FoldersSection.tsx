import { zodResolver } from "@hookform/resolvers/zod";
import { FolderOpen, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { FolderPicker } from "@/components/folders/FolderPicker";
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
type PickerTarget =
  | "watch_folder_path"
  | "trash_folder_path"
  | `allowed_root_folders.${number}.value`;

function BrowseButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button type="button" variant="outline" size="icon" onClick={onClick} aria-label={label}>
      <FolderOpen aria-hidden="true" />
    </Button>
  );
}

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
      watch_folder_path: settings.watch_folder_path,
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
    getValues,
    setValue,
    formState: { errors, isDirty },
  } = methods;
  const [pickerTarget, setPickerTarget] = useState<PickerTarget | null>(null);

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(values: FoldersFormValues) {
    updateSettings.mutate(
      {
        ...settings,
        allowed_root_folders: values.allowed_root_folders.map((r) => r.value.trim()),
        trash_folder_path: values.trash_folder_path.trim(),
        watch_folder_path: values.watch_folder_path.trim(),
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              allowed_root_folders: saved.allowed_root_folders.map((value) => ({ value })),
              trash_folder_path: saved.trash_folder_path,
              watch_folder_path: saved.watch_folder_path,
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
        <div className="space-y-1.5">
          <Label htmlFor="watch_folder_path">Watch folder</Label>
          <div className="flex items-center gap-2">
            <Input
              id="watch_folder_path"
              className="font-mono"
              placeholder="/Scans/Inbox"
              aria-invalid={!!errors.watch_folder_path}
              aria-describedby="watch_folder_path_help"
              {...register("watch_folder_path")}
            />
            <BrowseButton
              label="Browse for watch folder"
              onClick={() => setPickerTarget("watch_folder_path")}
            />
          </div>
          {errors.watch_folder_path ? (
            <p className="text-sm text-destructive">{errors.watch_folder_path.message}</p>
          ) : (
            <p id="watch_folder_path_help" className="text-sm text-muted-foreground">
              Where your scanner saves. New files here are added to the review queue. Documents
              already queued stay where they are if you change it.
            </p>
          )}
        </div>

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
                  <BrowseButton
                    label={`Browse for allowed root folder ${index + 1}`}
                    onClick={() => setPickerTarget(`allowed_root_folders.${index}.value`)}
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
          <div className="flex items-center gap-2">
            <Input
              id="trash_folder_path"
              className="font-mono"
              aria-invalid={!!errors.trash_folder_path}
              {...register("trash_folder_path")}
            />
            <BrowseButton
              label="Browse for trash folder"
              onClick={() => setPickerTarget("trash_folder_path")}
            />
          </div>
          {errors.trash_folder_path && (
            <p className="text-sm text-destructive">{errors.trash_folder_path.message}</p>
          )}
        </div>
      </SectionCard>
      <FolderPicker
        scope="all"
        open={pickerTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPickerTarget(null);
        }}
        value={pickerTarget ? getValues(pickerTarget) : ""}
        onSelect={(path) => {
          if (pickerTarget)
            setValue(pickerTarget, path, { shouldDirty: true, shouldValidate: true });
        }}
      />
    </form>
  );
}
