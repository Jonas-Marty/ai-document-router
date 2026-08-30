import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api/errors";
import type { AuthConfig } from "@/services/api/types";
import LoginPage from "./LoginPage";

vi.mock("@/services/api/client", () => ({
  apiClient: { getAuthConfig: vi.fn(), login: vi.fn(), register: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

const USER = { id: "u1", email: "owner@example.com", is_admin: true };

function config(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return {
    oidc_enabled: false,
    oidc_provider_name: "SSO",
    registration_open: false,
    has_users: true,
    ...overrides,
  };
}

function renderLogin(path = "/login") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("signs in with an email and password", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config());
    vi.mocked(apiClient.login).mockResolvedValue(USER);
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(apiClient.login).toHaveBeenCalled());
    expect(vi.mocked(apiClient.login).mock.calls[0]?.[0]).toEqual({
      email: "owner@example.com",
      password: "correct-horse-battery",
    });
    expect(apiClient.register).not.toHaveBeenCalled();
    // Registration is closed on this instance, so the screen must not offer it. Asserted
    // here because the awaited login call is a point where the config query has settled.
    expect(screen.queryByRole("button", { name: /need an account/i })).not.toBeInTheDocument();
  });

  it("registers instead when nobody has claimed the instance", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config({ has_users: false }));
    vi.mocked(apiClient.register).mockResolvedValue(USER);
    const user = userEvent.setup();
    renderLogin();

    expect(await screen.findByText("Create the first account")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: /create account and sign in/i }));

    await waitFor(() => expect(apiClient.register).toHaveBeenCalled());
    expect(apiClient.login).not.toHaveBeenCalled();
  });

  it("lets a second person register when ALLOW_REGISTRATION is on", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config({ registration_open: true }));
    vi.mocked(apiClient.register).mockResolvedValue(USER);
    const user = userEvent.setup();
    renderLogin();

    // The toggle only appears once the config query says registration is open.
    const toggle = await screen.findByRole("button", { name: /need an account/i });
    // Sign-in is still the default even so: having an account already is the common case.
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();

    await user.click(toggle);

    expect(screen.getByText("Create an account")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "second@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(apiClient.register).toHaveBeenCalled());
    expect(apiClient.login).not.toHaveBeenCalled();
  });

  it("drops the failed sign-in error when switching to registration", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config({ registration_open: true }));
    vi.mocked(apiClient.login).mockRejectedValue(
      new ApiError("invalid_credentials", "Wrong email or password.", 401),
    );
    const user = userEvent.setup();
    renderLogin();

    await user.type(await screen.findByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password-here");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong email or password.")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /need an account/i }));

    // Carrying it over would read as a verdict on the address just typed.
    expect(screen.queryByText("Wrong email or password.")).not.toBeInTheDocument();
  });

  it("shows the backend's reason verbatim when sign-in fails", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config());
    vi.mocked(apiClient.login).mockRejectedValue(
      new ApiError("invalid_credentials", "Wrong email or password.", 401),
    );
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Wrong email or password.")).toBeInTheDocument();
  });

  it("requires both fields before calling the API", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config());
    const user = userEvent.setup();
    renderLogin();

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Email is required.")).toBeInTheDocument();
    expect(screen.getByText("Password is required.")).toBeInTheDocument();
    expect(apiClient.login).not.toHaveBeenCalled();
  });

  it("surfaces a failure the identity provider redirected back with", async () => {
    vi.mocked(apiClient.getAuthConfig).mockResolvedValue(config({ oidc_enabled: true }));
    renderLogin("/login?error=Your%20account%20is%20not%20authorised");

    expect(await screen.findByText("Your account is not authorised")).toBeInTheDocument();
  });
});
