import { useCallback, useState } from "react";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint } from "@/services/api/types";
import { useListAiModels } from "./useSettings";

export interface ModelsByEndpoint {
  byEndpoint: Record<string, string[]>;
  errors: Record<string, string>;
  loadingId: string | null;
  load: (endpoint: AiEndpoint) => void;
}

/** One model list per endpoint, fetched the first time a dropdown pointing at it is opened.
 * Shared across every picker on a screen, so picking the same endpoint for reading and filing costs
 * one round trip to the provider rather than one per picker. Deliberately not a query: it is
 * a live probe of somebody else's server, not state this app owns. */
export function useModelsByEndpoint(): ModelsByEndpoint {
  const listModels = useListAiModels();
  const [byEndpoint, setByEndpoint] = useState<Record<string, string[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const load = useCallback(
    (endpoint: AiEndpoint) => {
      if (byEndpoint[endpoint.id] !== undefined || loadingId !== null) return;
      setLoadingId(endpoint.id);
      listModels.mutate(
        { base_url: endpoint.base_url, endpoint_id: endpoint.id },
        {
          onSuccess: ({ models }) => {
            setByEndpoint((current) => ({ ...current, [endpoint.id]: models }));
            setErrors((current) => {
              const { [endpoint.id]: _removed, ...rest } = current;
              return rest;
            });
            setLoadingId(null);
          },
          onError: (error) => {
            setErrors((current) => ({
              ...current,
              [endpoint.id]:
                error instanceof ApiError ? error.message : "Couldn't reach this endpoint.",
            }));
            setLoadingId(null);
          },
        },
      );
    },
    [byEndpoint, listModels, loadingId],
  );

  return { byEndpoint, errors, loadingId, load };
}
