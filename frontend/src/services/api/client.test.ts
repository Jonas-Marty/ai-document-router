import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpApiClient } from "./client";
import { ApiError } from "./errors";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("HttpApiClient error handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("carries the backend's request id on the error and logs it", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            409,
            { error: { code: "filename_collision", message: "Already there." } },
            { "X-Request-ID": "abc123" },
          ),
        ),
    );
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const error = await new HttpApiClient()
      .approveDocument("d1", { final_name: "x", final_folder_path: "/A", document_date: null })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).requestId).toBe("abc123");
    expect((error as ApiError).message).toBe("Already there.");
    expect(warn).toHaveBeenCalledWith(
      "[api] POST /documents/d1/approve 409 filename_collision (req abc123)",
      "Already there.",
    );
  });

  it("does not warn about a 401, which is just the signed-out state", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(401, { error: { code: "unauthenticated", message: "Sign in." } }),
        ),
    );
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "debug").mockImplementation(() => {});

    await expect(new HttpApiClient().getCurrentUser()).rejects.toBeInstanceOf(ApiError);
    expect(warn).not.toHaveBeenCalled();
  });
});
