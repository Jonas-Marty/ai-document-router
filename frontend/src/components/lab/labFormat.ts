import type { LabConfigurationSummary, LabField, LabRun } from "@/services/api/types";

export const FIELD_LABELS: Record<LabField, string> = {
  date: "Date",
  sender: "Sender",
  account: "Account",
  title: "Title",
  period: "Period",
  number: "Number",
  name: "Name",
  folder: "Folder",
};

const FIELD_ORDER: LabField[] = [
  "date",
  "sender",
  "account",
  "title",
  "period",
  "number",
  "name",
  "folder",
];

export function isLabField(value: string): value is LabField {
  return (FIELD_ORDER as string[]).includes(value);
}

/** The fields any configuration of a run was scored on, in name order with the folder last. */
export function scoredFields(summaries: LabConfigurationSummary[]): LabField[] {
  const seen = new Set(summaries.flatMap((summary) => Object.keys(summary.accuracy)));
  return FIELD_ORDER.filter((field) => seen.has(field));
}

export function formatShare(value: number | undefined): string {
  return value === undefined ? "–" : `${Math.round(value * 100)}%`;
}

export function formatSeconds(ms: number | null): string {
  if (ms === null) return "–";
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function formatTimeLeft(seconds: number | null): string | null {
  if (seconds === null) return null;
  if (seconds < 60) return "under a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  return `about ${hours} h ${minutes % 60} min left`;
}

export function configurationNames(run: LabRun): string {
  return run.summaries.map((summary) => summary.configuration_name).join(" · ");
}

// Matches the backend's limit (services/lab.py MAX_NAME_CHARS).
export const MAX_TEST_SET_NAME = 80;

export function testSetNameError(name: string): string | null {
  if (!name.trim()) return "Give the set a name.";
  if (name.trim().length > MAX_TEST_SET_NAME) return `At most ${MAX_TEST_SET_NAME} characters.`;
  return null;
}
