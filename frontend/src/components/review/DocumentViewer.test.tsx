import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DocumentViewer } from "./DocumentViewer";

const props = {
  documentId: "doc-1",
  filename: "invoice.pdf",
  mimeType: "application/pdf",
  fileSizeBytes: 12_345,
  pageCount: 2,
};

describe("DocumentViewer", () => {
  it("shows the compact card with filename and page count", () => {
    render(<DocumentViewer {...props} />);
    expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    expect(screen.getByText(/2 pages/)).toBeInTheDocument();
  });

  it("opens the full-screen viewer on tap and closes on X", async () => {
    const user = userEvent.setup();
    render(<DocumentViewer {...props} />);

    await user.click(screen.getByRole("button", { name: /invoice\.pdf/i }));
    expect(await screen.findByRole("button", { name: /close viewer/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /close viewer/i }));
    expect(screen.queryByRole("button", { name: /close viewer/i })).not.toBeInTheDocument();
  });

  it("falls back to an <img> for a non-PDF mime type", async () => {
    const user = userEvent.setup();
    render(<DocumentViewer {...props} mimeType="image/jpeg" filename="scan.jpg" />);

    await user.click(screen.getByRole("button", { name: /scan\.jpg/i }));
    expect(await screen.findByAltText("scan.jpg")).toBeInTheDocument();
    // Paging and zoom/rotate controls are PDF-only.
    expect(screen.queryByRole("button", { name: /zoom in/i })).not.toBeInTheDocument();
  });
});
