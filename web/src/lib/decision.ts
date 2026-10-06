import type { Priority } from "../api/types";

export interface ReviewState {
  priority: Priority | null; // chosen final priority
  priorityReason: string;
  protocol: string | null; // chosen final protocol id
  protocolReason: string;
  acknowledged: string[];
}

export interface DecisionBody {
  action: "approve";
  priority: { action: "accept" | "override"; value?: string; reason?: string };
  protocol: { action: "accept" | "override"; value?: string; reason?: string };
  acknowledged_flags: string[];
}

/** Turn the reviewer's choices into an API decision, or list what is missing. The API runs
 * the same checks; this only lets the UI explain them before the request is sent. */
export function buildDecision(
  state: ReviewState,
  ai: { priority: Priority | null; protocol: string | null; flags: string[] },
): { body: DecisionBody | null; problems: string[] } {
  const problems: string[] = [];
  const item = (kind: string, chosen: string | null, suggested: string | null, reason: string) => {
    if (!chosen) {
      problems.push(`Choose a ${kind}.`);
      return { action: "accept" as const };
    }
    if (chosen === suggested) return { action: "accept" as const };
    if (!reason.trim()) problems.push(`Give a reason for overriding the ${kind}.`);
    return { action: "override" as const, value: chosen, reason: reason.trim() };
  };
  const priority = item("priority", state.priority, ai.priority, state.priorityReason);
  const protocol = item("protocol", state.protocol, ai.protocol, state.protocolReason);
  const missing = ai.flags.filter((f) => !state.acknowledged.includes(f));
  if (missing.length) problems.push(`Acknowledge contrast flags: ${missing.join(", ")}.`);
  if (problems.length) return { body: null, problems };
  return {
    body: { action: "approve", priority, protocol, acknowledged_flags: state.acknowledged },
    problems,
  };
}
