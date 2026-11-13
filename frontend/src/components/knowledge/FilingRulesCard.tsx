import { useEffect, useState } from "react";
import { toast } from "sonner";
import { SectionCard } from "@/components/settings/SectionCard";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateKnowledge } from "@/hooks/useKnowledge";
import { ApiError } from "@/services/api/errors";
import type { ArchiveKnowledge } from "@/services/api/types";

// Mirrors MAX_FILING_RULES in backend/app/services/knowledge.py.
export const MAX_FILING_RULES = 4000;

/** SPEC 8.14: the owner's rules, given to every configuration's filing calls. */
export function FilingRulesCard({
  knowledge,
  onDirtyChange,
}: {
  knowledge: ArchiveKnowledge;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const update = useUpdateKnowledge();
  const saved = knowledge.filing_rules ?? "";
  const [rules, setRules] = useState(saved);
  const isDirty = rules !== saved;
  const tooLong = rules.trim().length > MAX_FILING_RULES;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (tooLong) return;
    update.mutate(
      { ...knowledge, filing_rules: rules.trim() || null },
      {
        onSuccess: (result) => {
          setRules(result.filing_rules ?? "");
          toast.success("Filing rules saved");
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : "Couldn't save the rules."),
      },
    );
  }

  return (
    <form onSubmit={onSubmit}>
      <SectionCard
        title="Filing rules"
        isDirty={isDirty}
        isSaving={update.isPending}
        onDiscard={() => setRules(saved)}
      >
        <div className="space-y-1.5">
          <Label htmlFor="filing-rules">Rules for naming and filing</Label>
          <Textarea
            id="filing-rules"
            rows={6}
            value={rules}
            aria-invalid={tooLong}
            aria-describedby="filing-rules-help"
            placeholder="Insurance policies go under /Versicherungen/<insurer>. Tax documents are filed by the tax year, not the date."
            onChange={(event) => setRules(event.target.value)}
          />
          <div className="flex flex-wrap justify-between gap-2 text-sm">
            <p id="filing-rules-help" className="text-muted-foreground">
              Every configuration is given these, whichever model it uses.
            </p>
            <p className={tooLong ? "text-destructive" : "text-muted-foreground"}>
              {rules.trim().length} / {MAX_FILING_RULES}
            </p>
          </div>
        </div>
      </SectionCard>
    </form>
  );
}
