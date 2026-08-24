import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceBadge, isLowConfidence } from "./ConfidenceBadge";

// SPEC 7.4 exact thresholds: >= 0.85 High, 0.60-0.84 Medium, < 0.60 Low.
describe("ConfidenceBadge", () => {
  it("labels the high-confidence boundary and above as High", () => {
    render(<ConfidenceBadge score={0.85} />);
    expect(screen.getByText(/high confidence · 85%/i)).toBeInTheDocument();
  });

  it("labels just below the high boundary as Medium", () => {
    render(<ConfidenceBadge score={0.84} />);
    expect(screen.getByText(/medium confidence · 84%/i)).toBeInTheDocument();
  });

  it("labels the medium-confidence boundary as Medium", () => {
    render(<ConfidenceBadge score={0.6} />);
    expect(screen.getByText(/medium confidence · 60%/i)).toBeInTheDocument();
  });

  it("labels just below the medium boundary as Low", () => {
    render(<ConfidenceBadge score={0.59} />);
    expect(screen.getByText(/low confidence · 59%/i)).toBeInTheDocument();
  });
});

describe("isLowConfidence", () => {
  it("matches the same 0.60 boundary the badge uses", () => {
    expect(isLowConfidence(0.6)).toBe(false);
    expect(isLowConfidence(0.59)).toBe(true);
  });
});
