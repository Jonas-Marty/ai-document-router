import { useCallback, useState } from "react";
import { AiEndpointsSection } from "@/components/settings/AiEndpointsSection";
import { AiTasksSection } from "@/components/settings/AiTasksSection";
import { FoldersSection } from "@/components/settings/FoldersSection";
import { NamingSection } from "@/components/settings/NamingSection";
import { OcrSection } from "@/components/settings/OcrSection";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { useSetNavigationGuard } from "@/hooks/useNavigationGuard";
import { useSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";

type SectionName = "folders" | "naming" | "endpoints" | "tasks" | "ocr";

export default function SettingsPage() {
  const settings = useSettings();
  const [dirtySections, setDirtySections] = useState<Record<SectionName, boolean>>({
    folders: false,
    naming: false,
    endpoints: false,
    tasks: false,
    ocr: false,
  });

  const anyDirty = Object.values(dirtySections).some(Boolean);
  useSetNavigationGuard(anyDirty ? "You have unsaved settings. Leave without saving?" : null);

  const onFoldersDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, folders: dirty })),
    [],
  );
  const onNamingDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, naming: dirty })),
    [],
  );
  const onEndpointsDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, endpoints: dirty })),
    [],
  );
  const onTasksDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, tasks: dirty })),
    [],
  );
  const onOcrDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, ocr: dirty })),
    [],
  );

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <h1 className="text-lg font-semibold">Settings</h1>

      {settings.isLoading ? (
        <SettingsSkeleton />
      ) : settings.isError ? (
        <ErrorState
          message={
            settings.error instanceof ApiError ? settings.error.message : "Couldn't load settings."
          }
          onRetry={() => settings.refetch()}
        />
      ) : settings.data ? (
        <>
          <FoldersSection settings={settings.data} onDirtyChange={onFoldersDirtyChange} />
          <NamingSection settings={settings.data} onDirtyChange={onNamingDirtyChange} />
          <AiEndpointsSection onDirtyChange={onEndpointsDirtyChange} />
          <AiTasksSection onDirtyChange={onTasksDirtyChange} />
          <OcrSection settings={settings.data} onDirtyChange={onOcrDirtyChange} />
        </>
      ) : null}
    </div>
  );
}

function SettingsSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true">
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
