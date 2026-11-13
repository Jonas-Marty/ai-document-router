import { useCallback, useState } from "react";
import { FilingRulesCard } from "@/components/knowledge/FilingRulesCard";
import { NameTemplateCard } from "@/components/knowledge/NameTemplateCard";
import { SendersCard } from "@/components/knowledge/SendersCard";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { useKnowledge } from "@/hooks/useKnowledge";
import { useSetNavigationGuard } from "@/hooks/useNavigationGuard";
import { ApiError } from "@/services/api/errors";

type CardName = "template" | "rules" | "senders";

/** SPEC 8.14: what is true of the archive whatever the model, shared by every configuration. */
export default function KnowledgePage() {
  const knowledge = useKnowledge();
  const [dirty, setDirty] = useState<Record<CardName, boolean>>({
    template: false,
    rules: false,
    senders: false,
  });
  useSetNavigationGuard(
    Object.values(dirty).some(Boolean) ? "You have unsaved changes. Leave without saving?" : null,
  );
  const onTemplateDirty = useCallback(
    (value: boolean) => setDirty((prev) => ({ ...prev, template: value })),
    [],
  );
  const onRulesDirty = useCallback(
    (value: boolean) => setDirty((prev) => ({ ...prev, rules: value })),
    [],
  );
  const onSendersDirty = useCallback(
    (value: boolean) => setDirty((prev) => ({ ...prev, senders: value })),
    [],
  );

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <h1 className="text-lg font-semibold">Archive knowledge</h1>
      <p className="text-sm text-muted-foreground">
        What holds for your archive whichever model files it. Every configuration in the Lab is
        given this.
      </p>
      {knowledge.isLoading ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : knowledge.isError ? (
        <ErrorState
          message={
            knowledge.error instanceof ApiError
              ? knowledge.error.message
              : "Couldn't load the archive knowledge."
          }
          onRetry={() => knowledge.refetch()}
        />
      ) : knowledge.data ? (
        <>
          <NameTemplateCard knowledge={knowledge.data} onDirtyChange={onTemplateDirty} />
          <FilingRulesCard knowledge={knowledge.data} onDirtyChange={onRulesDirty} />
        </>
      ) : null}
      <SendersCard onDirtyChange={onSendersDirty} />
    </div>
  );
}
