import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { ApiError } from "@/services/api/errors";
import type { Credentials } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

/** Who is signed in, or `null`. A 401 is the answer "nobody", not a failure -- so it
 * resolves rather than throwing, and never retries: retrying a 401 only delays the sign-in
 * screen. Any other error (the API is down) does propagate, so the outage banner still
 * wins over a spurious "please sign in". */
export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: async () => {
      try {
        return await apiClient.getCurrentUser();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    staleTime: 60_000,
    // Two components subscribe to this (RequireAuth and, once it lets them through, the
    // shell's sign-out control). With the default, the shell mounting would restart a query
    // that had just failed, which flips the guard back to "loading", which unmounts the
    // shell -- a spin that only stops when the API comes back. The session is a cookie the
    // server controls; revalidating on focus is enough.
    refetchOnMount: false,
  });
}

/** What the sign-in screen offers: SSO, registration, or both. Public, so it loads before
 * anyone is signed in. */
export function useAuthConfig() {
  return useQuery({
    queryKey: queryKeys.authConfig,
    queryFn: () => apiClient.getAuthConfig(),
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Credentials) => apiClient.login(body),
    onSuccess: (user) => {
      queryClient.setQueryData(queryKeys.currentUser, user);
      queryClient.invalidateQueries({ queryKey: queryKeys.authConfig });
    },
  });
}

export function useRegister() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Credentials) => apiClient.register(body),
    onSuccess: (user) => {
      queryClient.setQueryData(queryKeys.currentUser, user);
      queryClient.invalidateQueries({ queryKey: queryKeys.authConfig });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.logout(),
    onSuccess: () => {
      // Everything else in the cache was fetched as the person signing out, and the next
      // one must not see a flash of their queue -- so drop it all. The auth queries are
      // seeded instead of removed: an observer of a *removed* query keeps rendering its
      // last result, so clearing them would leave the shell up until something else
      // happened to refetch.
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
      // Refetched rather than set to null locally: the server decides who is signed in, and
      // asking it is also what re-reads `has_users`/`registration_open` for the screen the
      // person is about to land on. (Removing these two instead would leave their observers
      // rendering the last result -- a signed-out shell.)
      queryClient.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}
