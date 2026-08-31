import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { Label } from "@/components/ui/label";
import { useUpdateSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";

/** Its own section rather than a line in AI, because it is not about the model: it governs
 * the one thing in this app that writes file *content* to the WebDAV server. That deserves
 * to be findable, and to be switchable off without a redeploy.
 *
 * A native checkbox, not a shadcn one: shadcn's Checkbox is a Radix package, and CLAUDE.md
 * rule 8 puts a new runtime dependency behind a question. One control does not justify one. */
export function OcrSection({
  settings,
  onDirtyChange,
}: {
  settings: Settings;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const updateSettings = useUpdateSettings();
  // See FoldersSection's comment: `values` must stay reference-stable across incidental
  // re-renders, not a fresh object every time.
  const values = useMemo(() => ({ store_ocr_text: settings.store_ocr_text }), [settings]);
  const methods = useForm<{ store_ocr_text: boolean }>({
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const {
    register,
    handleSubmit,
    formState: { isDirty },
  } = methods;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(formValues: { store_ocr_text: boolean }) {
    updateSettings.mutate(
      { ...settings, store_ocr_text: formValues.store_ocr_text },
      {
        onSuccess: (saved) => {
          methods.reset({ store_ocr_text: saved.store_ocr_text }, { keepDirtyValues: false });
          toast.success("OCR settings saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save OCR settings.");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="OCR"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <div className="flex items-start gap-3">
          <input
            id="store_ocr_text"
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-primary"
            {...register("store_ocr_text")}
          />
          <div className="space-y-1">
            <Label htmlFor="store_ocr_text">File a searchable copy of scans</Label>
            <p className="text-sm text-muted-foreground">
              When a scan has no text layer, read it with OCR and file that version instead of the
              original, so the text can be selected and searched later. The page images are
              untouched — only invisible text is added. Documents that already have a text layer are
              filed exactly as they arrived.
            </p>
          </div>
        </div>
      </SectionCard>
    </form>
  );
}
