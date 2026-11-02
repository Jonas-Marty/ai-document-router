import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { UsageSummary } from "@/services/api/types";
import { TokenUsage } from "./TokenUsage";

const USAGE: UsageSummary = {
  extraction: {
    model_label: "Workshop PC · qwen2.5-vl",
    prompt_tokens: 2800,
    completion_tokens: 612,
    total_tokens: 3412,
    calls: 2,
    unreported_calls: 0,
  },
  filing: {
    model_label: "Infomaniak · gpt-4o",
    prompt_tokens: 1150,
    completion_tokens: 58,
    total_tokens: 1208,
    calls: 1,
    unreported_calls: 0,
  },
  total_tokens: 4620,
};

describe("TokenUsage", () => {
  it("renders nothing when no model was reached", () => {
    const { container } = render(<TokenUsage usage={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("summarises each task and the total, with the split one tap away", async () => {
    render(<TokenUsage usage={USAGE} />);

    const summary = screen.getByText("Tokens").closest("summary");
    expect(summary).toHaveTextContent("Tokens · extraction 3.4K · filing 1.2K · total 4.6K");

    await userEvent.setup().click(screen.getByText("Tokens"));
    expect(screen.getByText("· Workshop PC · qwen2.5-vl")).toBeVisible();
    expect(screen.getByText("2,800 prompt + 612 completion = 3,412 · 2 calls")).toBeVisible();
  });

  it("shows a task that didn't run as absent, and marks unreported totals as a minimum", () => {
    render(
      <TokenUsage
        usage={{
          extraction: null,
          filing: { ...(USAGE.filing as NonNullable<UsageSummary["filing"]>), unreported_calls: 1 },
          total_tokens: 1208,
        }}
      />,
    );

    const summary = screen.getByText("Tokens").closest("summary");
    expect(summary).toHaveTextContent("Tokens · filing 1.2K · total ≥ 1.2K");
    expect(summary).not.toHaveTextContent("extraction");
    expect(screen.getByText(/1 call didn't report usage/)).toBeInTheDocument();
  });
});
