import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "@/hooks/queryKeys";
import { ApiError } from "@/services/api/errors";
import { createQueryClient } from "./queryClient";

function unauthenticated() {
  return new ApiError("unauthenticated", "Sign in to continue.", 401);
}

describe("createQueryClient", () => {
  // spyOn hands back the existing mock when the prototype method is already spied, so
  // without this the second test inherits the first one's call history.
  afterEach(() => vi.restoreAllMocks());

  it("re-asks who is signed in when any query comes back 401", async () => {
    const client = createQueryClient();
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");

    await client
      .fetchQuery({ queryKey: ["queue"], queryFn: () => Promise.reject(unauthenticated()) })
      .catch(() => undefined);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.currentUser });
  });

  it("leaves the auth gate alone for an error that is not a 401", async () => {
    const client = createQueryClient();
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");

    await client
      .fetchQuery({
        queryKey: ["queue"],
        queryFn: () => Promise.reject(new ApiError("network_error", "Offline.", null)),
        // Overridden so the test does not sit through the retry backoff it is not testing;
        // the retry policy itself has its own case below.
        retry: false,
      })
      .catch(() => undefined);

    expect(invalidate).not.toHaveBeenCalled();
  });

  it("does not retry a 401, but does retry anything else", () => {
    const retry = createQueryClient().getDefaultOptions().queries?.retry;
    if (typeof retry !== "function") throw new Error("retry should be a predicate");

    expect(retry(0, unauthenticated())).toBe(false);
    expect(retry(0, new ApiError("webdav_unreachable", "Down.", 503))).toBe(true);
    expect(retry(3, new ApiError("webdav_unreachable", "Down.", 503))).toBe(false);
  });
});
