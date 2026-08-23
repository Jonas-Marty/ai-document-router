import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ThemeProvider } from "./components/layout/ThemeProvider";

function renderApp(initialPath = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <App />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function healthResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("routing", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse({ status: "ok", webdav_reachable: true, queue_depth: 0 }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the Review page at /", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Review" })).toBeInTheDocument();
  });

  it("renders the History page at /history", async () => {
    renderApp("/history");
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();
  });

  it("renders the Settings page at /settings", async () => {
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
  });

  it("navigates between routes via the top bar links", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Review" });

    await user.click(screen.getByRole("link", { name: /history/i }));
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: /settings/i }));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
  });
});

describe("dark mode", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse({ status: "ok", webdav_reachable: true, queue_depth: 0 }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("toggles the dark class on <html> and persists the choice", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByRole("button", { name: /change theme/i }));
    await user.click(await screen.findByText("Dark"));

    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });
    expect(localStorage.getItem("theme")).toBe("dark");

    await user.click(screen.getByRole("button", { name: /change theme/i }));
    await user.click(await screen.findByText("Light"));

    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(false);
    });
    expect(localStorage.getItem("theme")).toBe("light");
  });

  it("returning to System removes the stored override", async () => {
    localStorage.setItem("theme", "dark");
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByRole("button", { name: /change theme/i }));
    await user.click(await screen.findByText("System"));

    await waitFor(() => {
      expect(localStorage.getItem("theme")).toBeNull();
    });
  });
});

describe("outage banner (M6 acceptance: stopping the backend produces a banner, not a broken page)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows no banner when the backend and WebDAV are both healthy", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse({ status: "ok", webdav_reachable: true, queue_depth: 3 }),
    );
    renderApp("/");

    await screen.findByRole("heading", { name: "Review" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a WebDAV-specific message when the backend is up but WebDAV is not", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse({ status: "ok", webdav_reachable: false, queue_depth: 3 }),
    );
    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent(/webdav is unreachable/i);
    // The page itself still rendered -- not a broken page.
    expect(screen.getByRole("heading", { name: "Review" })).toBeInTheDocument();
  });

  it("shows a distinct message when the backend itself can't be reached at all", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the server/i);
    expect(screen.getByRole("heading", { name: "Review" })).toBeInTheDocument();
  });

  it("treats an empty-bodied 502 (Vite/nginx proxying to a stopped backend) as unreachable, not an API error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 502 }));
    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the server/i);
    expect(screen.getByRole("heading", { name: "Review" })).toBeInTheDocument();
  });
});
