import type {
  LabConfigurationSummary,
  LabResult,
  LabRun,
  LabRunDetail,
} from "@/services/api/types";

export function summary(
  id: string,
  name: string,
  overrides: Partial<LabConfigurationSummary> = {},
): LabConfigurationSummary {
  return {
    configuration_id: id,
    configuration_name: name,
    documents: 2,
    finished: 2,
    failed: 0,
    accuracy: { date: 1, sender: 1, title: 0.5, folder: 0.75 },
    fully_correct: 0.5,
    average_ms: 4200,
    prompt_tokens: 3000,
    completion_tokens: 400,
    cost: null,
    ...overrides,
  };
}

export function labRun(overrides: Partial<LabRun> = {}): LabRun {
  return {
    id: "run1",
    test_set_id: "set1",
    test_set_name: "Bank statements",
    status: "done",
    total: 2,
    finished: 2,
    waiting_reason: null,
    stage: null,
    remaining_seconds: null,
    created_at: "2026-11-14T10:00:00Z",
    finished_at: "2026-11-14T10:02:00Z",
    summaries: [summary("c1", "Daily")],
    ...overrides,
  };
}

export function labResult(overrides: Partial<LabResult> = {}): LabResult {
  return {
    id: "res1",
    test_case_id: "case1",
    position: 0,
    configuration_id: "c1",
    configuration_name: "Daily",
    original_filename: "scan_0001.pdf",
    expected_name: "2026.05.13 WIR Bank Kontoauszug",
    expected_date: "2026-05-13",
    expected_folder: "/Documents/Finance/WIR Bank/2026",
    expected_fields: {
      sender: "WIR Bank",
      account: null,
      title: "Kontoauszug",
      period: null,
      number: null,
    },
    status: "done",
    error: null,
    predicted_name: "2026.05.13 WIR Bank Kontoauszug",
    predicted_date: "2026-05-13",
    predicted_fields: {
      sender: "WIR Bank",
      account: null,
      title: "Kontoauszug",
      period: null,
      number: null,
    },
    predicted_folder: "/Documents/Finance/WIR Bank/2025",
    reading_label: "Text layer",
    duration_ms: 4100,
    verdicts: { date: 1, sender: 1, title: 1, folder: 0.5 },
    overrides: {},
    ...overrides,
  };
}

export function labRunDetail(
  run: Partial<LabRun> = {},
  results: LabResult[] = [labResult()],
): LabRunDetail {
  return { ...labRun(run), results };
}
