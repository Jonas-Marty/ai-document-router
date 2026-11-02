import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api/errors";
import type { AiTaskContext } from "@/services/api/types";
import { AiContextSection } from "./AiContextSection";

vi.mock("@/services/api/client", () => ({
  apiClient: { getAiContext: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

const CONTEXT: AiTaskContext[] = [
  {
    task: "extraction",
    chain: [],
    system_prompt: "You transcribe scanned documents into Markdown.",
    user_prompt: "Transcribe this document into Markdown.",
    note: "Sent with the first 2 pages of the document as images, 2 per request.",
  },
  {
    task: "filing",
    chain: ["Workshop PC · qwen3", "Infomaniak · gpt-4o"],
    system_prompt: "You file scanned documents.",
    user_prompt:
      "Folders available (choose exactly one):\n/Documents\n\nDocument text:\n[placeholder]",
    note: "The folder tree is read when each document is filed.",
  },
];

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AiContextSection />
    </QueryClientProvider>,
  );
}

describe("AiContextSection", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("fetches nothing until opened, then shows each task's chain and messages", async () => {
    vi.mocked(apiClient.getAiContext).mockResolvedValue(CONTEXT);
    const user = userEvent.setup();
    renderSection();

    expect(apiClient.getAiContext).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /what the models are given/i }));

    expect(await screen.findByText("Filing — choose the filename")).toBeInTheDocument();
    expect(apiClient.getAiContext).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Workshop PC · qwen3 → Infomaniak · gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("no endpoint assigned yet")).toBeInTheDocument();
    expect(screen.getByText(/Folders available \(choose exactly one\)/)).toBeInTheDocument();
    expect(screen.getByText("You transcribe scanned documents into Markdown.")).toBeInTheDocument();
  });

  it("refetches on Refresh", async () => {
    vi.mocked(apiClient.getAiContext).mockResolvedValue(CONTEXT);
    const user = userEvent.setup();
    renderSection();

    await user.click(screen.getByRole("button", { name: /what the models are given/i }));
    await screen.findByText("Filing — choose the filename");
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(apiClient.getAiContext).toHaveBeenCalledTimes(2);
  });

  it("shows the backend's reason when it can't be built", async () => {
    vi.mocked(apiClient.getAiContext).mockRejectedValue(
      new ApiError("webdav_unreachable", "Can't reach the WebDAV server.", 503),
    );
    const user = userEvent.setup();
    renderSection();

    await user.click(screen.getByRole("button", { name: /what the models are given/i }));

    expect(await screen.findByText("Can't reach the WebDAV server.")).toBeInTheDocument();
  });
});
