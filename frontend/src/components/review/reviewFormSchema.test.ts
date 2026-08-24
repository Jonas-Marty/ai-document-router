import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it } from "vitest";
import { reviewFormSchema } from "./reviewFormSchema";

// Exercises the schema through the exact resolver react-hook-form calls, not just
// schema.safeParse -- the two have disagreed before across zod major versions.
const resolver = zodResolver(reviewFormSchema);

describe("reviewFormSchema via zodResolver", () => {
  it("accepts a fully valid form", async () => {
    const result = await resolver(
      { documentDate: "2026-05-18", name: "Reka Kartenersatz", folderPath: "/Documents/Finance" },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );
    expect(result.errors).toEqual({});
  });

  it("surfaces the SPEC 7.1 message for an invalid name", async () => {
    const result = await resolver(
      { documentDate: "", name: "bad/name", folderPath: "/Documents/Finance" },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );
    expect(result.errors.name?.message).toMatch(/can't contain/);
  });

  it("requires a non-empty folder path", async () => {
    const result = await resolver(
      { documentDate: "", name: "Invoice", folderPath: "" },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );
    expect(result.errors.folderPath?.message).toMatch(/choose a target folder/i);
  });
});
