import { useLabRuns } from "@/hooks/useLab";
import type { LabConfigurationSummary, LabField, LabRun } from "@/services/api/types";
import { FIELD_LABELS, formatShare, scoredFields } from "./labFormat";

interface Latest {
  run: LabRun;
  summary: LabConfigurationSummary;
}

/** The newest finished run a configuration took part in. Runs come newest first. */
function latestFor(runs: LabRun[], configurationId: string): Latest | null {
  for (const run of runs) {
    if (run.status !== "done") continue;
    const summary = run.summaries.find((row) => row.configuration_id === configurationId);
    if (summary) return { run, summary };
  }
  return null;
}

/** SPEC 8.13a: before a configuration becomes the default, how it and the current default did
 * in their last finished runs. Possibly different runs on different sets, so each column
 * names its set. */
export function LastRunsComparison({
  candidate,
  current,
}: {
  candidate: { id: string; name: string };
  current: { id: string; name: string } | undefined;
}) {
  const runs = useLabRuns();
  if (!runs.data) return null;
  const columns = [
    { id: candidate.id, name: candidate.name, latest: latestFor(runs.data, candidate.id) },
    ...(current
      ? [{ id: current.id, name: current.name, latest: latestFor(runs.data, current.id) }]
      : []),
  ];
  const ran = columns.filter((column) => column.latest !== null);
  if (ran.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Neither has been run in the Lab yet, so there is nothing to compare.
      </p>
    );
  }
  const fields: LabField[] = scoredFields(
    ran.flatMap((column) => (column.latest ? [column.latest.summary] : [])),
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Last finished runs</caption>
        <thead>
          <tr className="border-b">
            <th scope="col" className="py-1 pr-2 text-left font-medium">
              <span className="sr-only">Field</span>
            </th>
            {columns.map((column) => (
              <th key={column.id} scope="col" className="px-2 py-1 text-right align-bottom">
                <span className="block font-medium break-words">{column.name}</span>
                <span className="block text-xs font-normal text-muted-foreground">
                  {column.latest ? column.latest.run.test_set_name : "not run yet"}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field}>
              <th scope="row" className="py-0.5 pr-2 text-left font-normal text-muted-foreground">
                {FIELD_LABELS[field]}
              </th>
              {columns.map((column) => (
                <td key={column.id} className="px-2 py-0.5 text-right tabular-nums">
                  {formatShare(column.latest?.summary.accuracy[field])}
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t">
            <th scope="row" className="py-0.5 pr-2 text-left font-medium">
              Fully correct
            </th>
            {columns.map((column) => (
              <td key={column.id} className="px-2 py-0.5 text-right font-medium tabular-nums">
                {formatShare(column.latest?.summary.fully_correct)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
