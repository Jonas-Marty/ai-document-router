import { useCallback, useState } from "react";
import { AiEndpointsSection } from "@/components/settings/AiEndpointsSection";
import { AnalysisSection } from "@/components/settings/AnalysisSection";
import { FoldersSection } from "@/components/settings/FoldersSection";
import { NamingSection } from "@/components/settings/NamingSection";
import { NextcloudSection } from "@/components/settings/NextcloudSection";
import { OcrSection } from "@/components/settings/OcrSection";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { useSetNavigationGuard } from "@/hooks/useNavigationGuard";
import { useSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";

type SectionName = "nextcloud" | "folders" | "naming" | "endpoints" | "analysis" | "ocr";

export default function SettingsPage() {
  const settings = useSettings();
  const [dirtySections, setDirtySections] = useState<Record<SectionName, boolean>>({
    nextcloud: false,
    folders: false,
    naming: false,
    endpoints: false,
    analysis: false,
    ocr: false,
  });

  const anyDirty = Object.values(dirtySections).some(Boolean);
  useSetNavigationGuard(anyDirty ? "You have unsaved settings. Leave without saving?" : null);

  const onNextcloudDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, nextcloud: dirty })),
    [],
  );
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
  const onAnalysisDirtyChange = useCallback(
    (dirty: boolean) => setDirtySections((prev) => ({ ...prev, analysis: dirty })),
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
          <NextcloudSection onDirtyChange={onNextcloudDirtyChange} />
          <FoldersSection settings={settings.data} onDirtyChange={onFoldersDirtyChange} />
          <NamingSection settings={settings.data} onDirtyChange={onNamingDirtyChange} />
          <AiEndpointsSection onDirtyChange={onEndpointsDirtyChange} />
          <AnalysisSection settings={settings.data} onDirtyChange={onAnalysisDirtyChange} />
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
