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

const SIGNED_IN = { id: "u1", email: "owner@example.com", is_admin: true };
const AUTH_CONFIG = {
  oidc_enabled: false,
  oidc_provider_name: "SSO",
  registration_open: false,
  has_users: true,
};

/** Routes fetch by path instead of answering every call the same way: with the auth gate in
 * front of the app, a test that wants to see a page has to be signed in first. `health`
 * stays a callback so the outage tests can vary just that one response. */
function mockApi(health: () => Response, auth: unknown = SIGNED_IN, authStatus = 200) {
  // Signing out has to change what /auth/me answers, or the gate would just let the person
  // straight back in on the refetch.
  let signedOut = false;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/logout")) {
      signedOut = true;
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url.includes("/auth/me")) {
      return signedOut
        ? Promise.resolve(
            healthResponse({ error: { code: "unauthenticated", message: "no" } }, 401),
          )
        : Promise.resolve(healthResponse(auth, authStatus));
    }
    if (url.includes("/auth/config")) return Promise.resolve(healthResponse(AUTH_CONFIG));
    if (url.includes("/health")) return Promise.resolve(health());
    if (url.includes("/queue")) {
      return Promise.resolve(healthResponse({ items: [], total_pending: 0 }));
    }
    if (url.includes("/history")) {
      return Promise.resolve(healthResponse({ items: [], next_cursor: null }));
    }
    if (url.includes("/ai/endpoints")) return Promise.resolve(healthResponse([]));
    if (url.includes("/ai/tasks")) {
      return Promise.resolve(
        healthResponse([
          { task: "extraction", steps: [] },
          { task: "filing", steps: [] },
        ]),
      );
    }
    if (url.includes("/settings")) {
      return Promise.resolve(
        healthResponse({
          allowed_root_folders: ["/Documents"],
          trash_folder_path: "/Trash",
          filename_pattern: null,
          filename_pattern_hint: null,
          store_ocr_text: true,
        }),
      );
    }
    return Promise.resolve(healthResponse({}));
  });
}

const healthy = () => healthResponse({ status: "ok", webdav_reachable: true, queue_depth: 0 });

describe("routing", () => {
  beforeEach(() => mockApi(healthy));
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
    mockApi(healthy);
  });
  afterEach(() => vi.restoreAllMocks());

  it("toggles the dark class on <html> and persists the choice", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Review" });

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
    await screen.findByRole("heading", { name: "Review" });

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
    mockApi(() => healthResponse({ status: "ok", webdav_reachable: true, queue_depth: 3 }));
    renderApp("/");

    await screen.findByRole("heading", { name: "Review" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a WebDAV-specific message when the backend is up but WebDAV is not", async () => {
    mockApi(() => healthResponse({ status: "ok", webdav_reachable: false, queue_depth: 3 }));
    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent(/webdav is unreachable/i);
    // The page itself still rendered -- not a broken page.
    expect(screen.getByRole("heading", { name: "Review" })).toBeInTheDocument();
  });

  it("shows a distinct message when the backend itself can't be reached at all", async () => {
    // Every call fails, /auth/me included: an unreachable API must surface as the outage
    // banner over a still-rendered page, not as "please sign in".
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

describe("authentication gate", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the sign-in screen instead of the app when nobody is signed in", async () => {
    mockApi(healthy, { error: { code: "unauthenticated", message: "Sign in to continue." } }, 401);
    renderApp("/settings");

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    // The protected screen must not render behind it, not even briefly.
    expect(screen.queryByRole("heading", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /history/i })).not.toBeInTheDocument();
  });

  it("offers first-account setup on an instance nobody has claimed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/me")) return Promise.resolve(healthResponse({}, 401));
      if (url.includes("/auth/config"))
        return Promise.resolve(healthResponse({ ...AUTH_CONFIG, has_users: false }));
      return Promise.resolve(healthy());
    });
    renderApp("/");

    expect(await screen.findByText("Create the first account")).toBeInTheDocument();
    expect(screen.getByText(/first account becomes its admin/i)).toBeInTheDocument();
  });

  it("offers the configured provider when SSO is enabled", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/me")) return Promise.resolve(healthResponse({}, 401));
      if (url.includes("/auth/config"))
        return Promise.resolve(
          healthResponse({ ...AUTH_CONFIG, oidc_enabled: true, oidc_provider_name: "Authentik" }),
        );
      return Promise.resolve(healthy());
    });
    renderApp("/");

    const sso = await screen.findByRole("link", { name: /sign in with authentik/i });
    // A full navigation, so the provider can redirect the browser back.
    expect(sso).toHaveAttribute("href", "/api/v1/auth/oidc/login");
  });

  it("signs out back to the sign-in screen", async () => {
    mockApi(healthy);
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Review" });

    await user.click(screen.getByRole("button", { name: /sign out owner@example.com/i }));

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});
