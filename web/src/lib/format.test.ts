import { describe, expect, it } from "vitest";
import { displayValue, duration, isUnderTriage, parseFieldInput, pct } from "./format";

describe("duration", () => {
  it("formats seconds, minutes, hours and days", () => {
    expect(duration(42)).toBe("42s");
    expect(duration(125)).toBe("2m");
    expect(duration(3 * 3600 + 120)).toBe("3h 2m");
    expect(duration(3 * 86400 + 3600)).toBe("3d 1h");
  });
});

describe("isUnderTriage", () => {
  it("flags suggestions less urgent than gold", () => {
    expect(isUnderTriage("P1", "P2")).toBe(true);
    expect(isUnderTriage("P2", "P1")).toBe(false);
    expect(isUnderTriage("P3", "P3")).toBe(false);
    expect(isUnderTriage("P2", "none")).toBe(true);
  });
});

describe("parseFieldInput", () => {
  it("parses lists, numbers, booleans and blanks", () => {
    expect(parseFieldInput("allergies", "Penicillin\n\n Latex ")).toEqual(["Penicillin", "Latex"]);
    expect(parseFieldInput("egfr", "72")).toBe(72);
    expect(parseFieldInput("contrast_requested", "Yes")).toBe(true);
    expect(parseFieldInput("dob", "  ")).toBeNull();
  });
});

describe("display helpers", () => {
  it("renders empty values as a dash", () => {
    expect(displayValue(null)).toBe("—");
    expect(displayValue([])).toBe("—");
    expect(displayValue(false)).toBe("No");
    expect(pct(0.9567)).toBe("95.7%");
    expect(pct(null)).toBe("–");
  });
});
