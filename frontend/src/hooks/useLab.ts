import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type {
  AddCasesRequest,
  LabField,
  LabRunDetail,
  StartRunRequest,
} from "@/services/api/types";
import { queryKeys } from "./queryKeys";

// SPEC 8.13a: a running run is followed every 3 s; a finished one never changes on its own.
const RUNNING_INTERVAL_MS = 3_000;

export function useLabRuns() {
  return useQuery({
    queryKey: queryKeys.labRuns,
    queryFn: () => apiClient.listLabRuns(),
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === "running") ? RUNNING_INTERVAL_MS : false,
  });
}

export function useLabRun(id: string) {
  return useQuery({
    queryKey: queryKeys.labRun(id),
    queryFn: () => apiClient.getLabRun(id),
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? RUNNING_INTERVAL_MS : false,
  });
}

export function useTestSets() {
  return useQuery({
    queryKey: queryKeys.testSets,
    queryFn: () => apiClient.listTestSets(),
  });
}

export function useTestSet(id: string) {
  return useQuery({
    queryKey: queryKeys.testSet(id),
    queryFn: () => apiClient.getTestSet(id),
  });
}

/** Sets, cases and runs point at each other (a run names its set, a set counts its cases), so
 * every Lab write refreshes the whole Lab: it is a handful of small queries. */
function useLabMutation<TVariables, TData>(fn: (variables: TVariables) => Promise<TData>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.lab }),
  });
}

export function useCreateTestSet() {
  return useLabMutation((name: string) => apiClient.createTestSet(name));
}

export function useRenameTestSet() {
  return useLabMutation(({ id, name }: { id: string; name: string }) =>
    apiClient.renameTestSet(id, name),
  );
}

export function useDeleteTestSet() {
  return useLabMutation((id: string) => apiClient.deleteTestSet(id));
}

export function useAddTestCases() {
  return useLabMutation(({ id, body }: { id: string; body: AddCasesRequest }) =>
    apiClient.addTestCases(id, body),
  );
}

export function useDeleteTestCase() {
  return useLabMutation((id: string) => apiClient.deleteTestCase(id));
}

export function useStartLabRun() {
  return useLabMutation((body: StartRunRequest) => apiClient.startLabRun(body));
}

export function useCancelLabRun() {
  return useLabMutation((id: string) => apiClient.cancelLabRun(id));
}

export function useDeleteLabRun() {
  return useLabMutation((id: string) => apiClient.deleteLabRun(id));
}

/** Overruling one verdict changes the run's summary too, so the run is refetched rather than
 * patched: the scoring rules live in one place, the backend. */
export function useSetLabVerdict(runId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, field, score }: { id: string; field: LabField; score: number | null }) =>
      apiClient.setLabVerdict(id, field, score),
    onSuccess: (result) => {
      queryClient.setQueryData<LabRunDetail>(
        queryKeys.labRun(runId),
        (run) =>
          run && {
            ...run,
            results: run.results.map((row) => (row.id === result.id ? result : row)),
          },
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.labRuns });
    },
  });
}
