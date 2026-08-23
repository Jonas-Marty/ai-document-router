import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the API status once the health check resolves", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", webdav_reachable: true, queue_depth: 0 }), {
        status: 200,
      }),
    );

    renderApp();

    expect(await screen.findByText("API status: ok")).toBeInTheDocument();
  });

  it("shows an error when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 503 }));

    renderApp();

    await waitFor(() => {
      expect(screen.getByText("Backend unreachable")).toBeInTheDocument();
    });
  });
});
