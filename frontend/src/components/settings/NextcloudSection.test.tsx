import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NextcloudConnection } from "@/services/api/types";
import { NextcloudSection } from "./NextcloudSection";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getNextcloud: vi.fn(),
    updateNextcloud: vi.fn(),
    testNextcloud: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

const CONNECTED: NextcloudConnection = {
  base_url: "https://cloud.example.com/remote.php/dav/files/me",
  username: "me",
  password_set: true,
};

function renderSection(connection: NextcloudConnection) {
  vi.mocked(apiClient.getNextcloud).mockResolvedValue(connection);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <NextcloudSection onDirtyChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("NextcloudSection", () => {
  afterEach(() => vi.clearAllMocks());

  it("never shows the stored app password, only that one is saved", async () => {
    renderSection(CONNECTED);

    const password = await screen.findByLabelText("App password");
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("type", "password");
    expect(screen.getByText(/leave blank to keep/i)).toBeInTheDocument();
  });

  it("keeps the stored password when saving with the field left blank", async () => {
    const user = userEvent.setup();
    vi.mocked(apiClient.updateNextcloud).mockResolvedValue({ ...CONNECTED, username: "me2" });
    renderSection(CONNECTED);

    const username = await screen.findByLabelText("Username");
    await user.clear(username);
    await user.type(username, "me2");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(apiClient.updateNextcloud).toHaveBeenCalledWith({
        base_url: CONNECTED.base_url,
        username: "me2",
      }),
    );
  });

  it("asks for an app password before the first save", async () => {
    const user = userEvent.setup();
    renderSection({ base_url: "", username: "", password_set: false });

    await user.type(
      await screen.findByLabelText("WebDAV URL"),
      "https://cloud.example.com/remote.php/dav/files/me",
    );
    await user.type(screen.getByLabelText("Username"), "me");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Enter an app password.")).toBeInTheDocument();
    expect(apiClient.updateNextcloud).not.toHaveBeenCalled();
  });

  it("tests what is typed, not what is saved", async () => {
    const user = userEvent.setup();
    vi.mocked(apiClient.testNextcloud).mockResolvedValue(undefined);
    renderSection(CONNECTED);

    await user.type(await screen.findByLabelText("App password"), "new-app-password");
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() =>
      expect(apiClient.testNextcloud).toHaveBeenCalledWith({
        base_url: CONNECTED.base_url,
        username: "me",
        password: "new-app-password",
      }),
    );
  });
});
