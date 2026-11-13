import { ArrowDown, ArrowUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { SectionCard } from "@/components/settings/SectionCard";
import { Button } from "@/components/ui/button";
import { useUpdateKnowledge } from "@/hooks/useKnowledge";
import {
  ALWAYS_IN_TEMPLATE,
  assembleName,
  DEFAULT_TEMPLATE,
  FIELD_LABELS,
  type TemplateField,
} from "@/lib/naming";
import { ApiError } from "@/services/api/errors";
import type { ArchiveKnowledge } from "@/services/api/types";

interface Row {
  key: TemplateField;
  included: boolean;
}

const LABELS: Record<TemplateField, string> = { date: "Date", ...FIELD_LABELS };

// SPEC 8.14: a live example from sample values, so the order can be judged at a glance.
const SAMPLE = {
  sender: "WIR Bank",
  account: "738.953-48.5800",
  title: "Kontoauszug",
  period: "01.04.2026 - 30.04.2026",
  number: "4711",
};

/** The template as rows: what it includes in its order, then the fields it leaves out. */
function rowsOf(template: readonly TemplateField[]): Row[] {
  const left = DEFAULT_TEMPLATE.filter((key) => !template.includes(key));
  return [
    ...template.map((key) => ({ key, included: true })),
    ...left.map((key) => ({ key, included: false })),
  ];
}

function templateOf(rows: Row[]): TemplateField[] {
  return rows.filter((row) => row.included).map((row) => row.key);
}

/** SPEC 8.14: the order of the name's fields, and which optional ones are in it. */
export function NameTemplateCard({
  knowledge,
  onDirtyChange,
}: {
  knowledge: ArchiveKnowledge;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const update = useUpdateKnowledge();
  const saved = useMemo(() => rowsOf(knowledge.name_template), [knowledge.name_template]);
  const [rows, setRows] = useState(saved);
  const isDirty = JSON.stringify(rows) !== JSON.stringify(saved);

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function move(index: number, by: -1 | 1) {
    setRows((current) => {
      const next = [...current];
      const [row] = next.splice(index, 1);
      if (row) next.splice(index + by, 0, row);
      return next;
    });
  }

  function toggle(index: number) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, included: !row.included } : row)),
    );
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    update.mutate(
      { ...knowledge, name_template: templateOf(rows) },
      {
        onSuccess: (result) => {
          setRows(rowsOf(result.name_template));
          toast.success("Name template saved");
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : "Couldn't save the template."),
      },
    );
  }

  const example = assembleName("2026-05-13", SAMPLE, templateOf(rows));

  return (
    <form onSubmit={onSubmit}>
      <SectionCard
        title="Name template"
        isDirty={isDirty}
        isSaving={update.isPending}
        onDiscard={() => setRows(saved)}
      >
        <p className="text-sm text-muted-foreground">
          The order the parts of a name are joined in. Date, sender and title are always in; the
          others can be left out.
        </p>
        <ol className="divide-y divide-border rounded-md border border-border">
          {rows.map((row, index) => {
            const always = ALWAYS_IN_TEMPLATE.has(row.key);
            const label = LABELS[row.key];
            return (
              <li key={row.key} className="flex min-h-11 items-center gap-2 px-3 py-1">
                <span className={`min-w-0 flex-1 ${row.included ? "" : "text-muted-foreground"}`}>
                  {label}
                </span>
                {always ? (
                  <span className="text-xs text-muted-foreground">Always in</span>
                ) : (
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={row.included}
                      onChange={() => toggle(index)}
                    />
                    Include
                  </label>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Move ${label} up`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Move ${label} down`}
                  disabled={index === rows.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown aria-hidden="true" />
                </Button>
              </li>
            );
          })}
        </ol>
        <div className="space-y-1">
          <p className="text-sm text-muted-foreground">Example</p>
          <output
            aria-label="Example name"
            className="block break-words rounded-md border border-dashed border-border px-2.5 py-1.5 font-mono text-sm"
          >
            {example}.pdf
          </output>
        </div>
      </SectionCard>
    </form>
  );
}
