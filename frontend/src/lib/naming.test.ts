import { describe, expect, it } from "vitest";
import { isWithinAllowedRoot, trimStemOnBlur, validateFolderName, validateStem } from "./naming";

describe("validateStem", () => {
  it("accepts an ordinary name", () => {
    expect(validateStem("2026.05.18 Reka Kartenersatz")).toBeNull();
  });

  it("rejects empty or whitespace-only input", () => {
    expect(validateStem("")).toMatch(/required/i);
    expect(validateStem("   ")).toMatch(/required/i);
  });

  it("rejects names over 200 characters", () => {
    expect(validateStem("a".repeat(201))).toMatch(/200 characters or fewer/);
    expect(validateStem("a".repeat(200))).toBeNull();
  });

  it("rejects each SPEC 7.1 forbidden character", () => {
    for (const ch of ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]) {
      expect(validateStem(`bad${ch}name`)).toMatch(/can't contain/);
    }
  });

  it("rejects a literal '..' sequence", () => {
    expect(validateStem("a..b")).toMatch(/can't contain '\.\.'/);
  });

  it("rejects a leading or trailing dot, space, or hyphen", () => {
    expect(validateStem(".leading-dot")).toMatch(/dot, space, or hyphen/);
    expect(validateStem("-leading-hyphen")).toMatch(/dot, space, or hyphen/);
    expect(validateStem("trailing-dot.")).toMatch(/dot, space, or hyphen/);
    expect(validateStem("trailing-hyphen-")).toMatch(/dot, space, or hyphen/);
  });

  it("rejects control characters", () => {
    expect(validateStem(`bad${String.fromCharCode(7)}name`)).toMatch(/control characters/);
  });

  it("trims plain surrounding whitespace before validating", () => {
    expect(validateStem("  a valid name  ")).toBeNull();
  });
});

describe("validateFolderName", () => {
  it("rejects a slash (already caught by the shared forbidden-character set)", () => {
    expect(validateFolderName("a/b")).toMatch(/can't contain \//);
  });

  it("caps at 100 characters, not the stem's 200", () => {
    expect(validateFolderName("a".repeat(101))).toMatch(/100 characters or fewer/);
    expect(validateFolderName("a".repeat(100))).toBeNull();
  });
});

describe("trimStemOnBlur", () => {
  it("silently strips edge dot/space/hyphen without erroring", () => {
    expect(trimStemOnBlur("  .-Invoice-.  ")).toBe("Invoice");
  });
});

describe("isWithinAllowedRoot", () => {
  const roots = ["/Documents/Finance", "/Documents/Personal"];

  it("accepts an allowed root itself and paths nested under it", () => {
    expect(isWithinAllowedRoot("/Documents/Finance", roots)).toBe(true);
    expect(isWithinAllowedRoot("/Documents/Finance/2026", roots)).toBe(true);
  });

  it("rejects a path outside every allowed root, including a lookalike prefix", () => {
    expect(isWithinAllowedRoot("/Documents/Other", roots)).toBe(false);
    expect(isWithinAllowedRoot("/Documents/FinanceOverflow", roots)).toBe(false);
  });
});
