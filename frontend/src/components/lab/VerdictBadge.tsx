import { Check, Contrast, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { LabField } from "@/services/api/types";
import { FIELD_LABELS } from "./labFormat";

function verdictWord(score: number): string {
  if (score >= 1) return "right";
  if (score > 0) return "partly right";
  return "wrong";
}

function VerdictIcon({ score }: { score: number }) {
  if (score >= 1) return <Check aria-hidden="true" />;
  if (score > 0) return <Contrast aria-hidden="true" />;
  return <X aria-hidden="true" />;
}

/** SPEC 8.13a: one field's verdict. The owner can overrule it; an overruled badge is outlined
 * so a score nobody computed is never mistaken for one the rules gave. */
export function VerdictBadge({
  field,
  automatic,
  override,
  disabled,
  onChange,
}: {
  field: LabField;
  automatic: number;
  override: number | undefined;
  disabled: boolean;
  onChange: (score: number | null) => void;
}) {
  const score = override ?? automatic;
  const label = FIELD_LABELS[field];
  const choices: { score: number; label: string }[] = [
    { score: 1, label: "Right" },
    // Half a point exists only for the folder (a year apart, or the right parent).
    ...(field === "folder" ? [{ score: 0.5, label: "Partly right" }] : []),
    { score: 0, label: "Wrong" },
  ];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          type="button"
          className="rounded-4xl focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          aria-label={`${label}: ${verdictWord(score)}${override !== undefined ? ", overruled" : ""}. Change`}
        >
          <Badge
            variant={override !== undefined ? "outline" : score >= 1 ? "secondary" : "ghost"}
            className={score < 1 ? "text-muted-foreground" : undefined}
          >
            <VerdictIcon score={score} />
            {label}
          </Badge>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        {choices.map((choice) => (
          <DropdownMenuItem key={choice.score} onSelect={() => onChange(choice.score)}>
            <VerdictIcon score={choice.score} />
            {choice.label}
            {score === choice.score && <span className="sr-only"> (current)</span>}
          </DropdownMenuItem>
        ))}
        {override !== undefined && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onChange(null)}>
              Back to automatic ({verdictWord(automatic)})
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
