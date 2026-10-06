import { describe, expect, it } from "vitest";
import { buildDecision, type ReviewState } from "./decision";

const ai = { priority: "P2" as const, protocol: "MRI-KNEE", flags: ["egfr_low"] };
const accepted: ReviewState = {
  priority: "P2",
  priorityReason: "",
  protocol: "MRI-KNEE",
  protocolReason: "",
  acknowledged: ["egfr_low"],
};

describe("buildDecision", () => {
  it("accepts AI suggestions when unchanged", () => {
    const { body, problems } = buildDecision(accepted, ai);
    expect(problems).toEqual([]);
    expect(body?.priority).toEqual({ action: "accept" });
    expect(body?.protocol).toEqual({ action: "accept" });
  });

  it("requires a reason for an override", () => {
    const { body, problems } = buildDecision({ ...accepted, priority: "P1" }, ai);
    expect(body).toBeNull();
    expect(problems[0]).toMatch(/reason for overriding the priority/);
  });

  it("builds an override with its reason", () => {
    const { body } = buildDecision(
      { ...accepted, protocol: "MRI-HIP", protocolReason: "Hip, not knee" },
      ai,
    );
    expect(body?.protocol).toEqual({ action: "override", value: "MRI-HIP", reason: "Hip, not knee" });
  });

  it("blocks approval until every contrast flag is acknowledged", () => {
    const { problems } = buildDecision({ ...accepted, acknowledged: [] }, ai);
    expect(problems).toContain("Acknowledge contrast flags: egfr_low.");
  });

  it("needs a choice when the AI made none", () => {
    const { problems } = buildDecision(
      { ...accepted, priority: null },
      { ...ai, priority: null },
    );
    expect(problems).toContain("Choose a priority.");
  });
});
