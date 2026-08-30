import { QueryCache, QueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/hooks/queryKeys";
import { ApiError } from "@/services/api/errors";

const MAX_RETRIES = 3;

function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** The app's single QueryClient.
 *
 * Built here rather than inline in `main.tsx` so the session-expiry behaviour below is
 * reachable from a test. */
export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      // A session can end while the app is open -- it expires, or it is signed out in
      // another tab. Without this every screen would sit on an error state until something
      // happened to refetch `/auth/me`, because only that one query feeds the auth gate.
      // Re-asking who is signed in is what turns those 401s into the sign-in screen.
      onError: (error) => {
        if (isUnauthenticated(error)) {
          client.invalidateQueries({ queryKey: queryKeys.currentUser });
        }
      },
    }),
    defaultOptions: {
      queries: {
        // A 401 is an answer, not a transient failure. Retrying it three times with backoff
        // only delays the sign-in screen it should have produced immediately.
        retry: (failureCount, error) => !isUnauthenticated(error) && failureCount < MAX_RETRIES,
      },
    },
  });
  return client;
}
