import type { Priority } from "../api/types";

export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "–" : `${(value * 100).toFixed(digits)}%`;
}

export function usd(value: number | null | undefined): string {
  if (value == null) return "–";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(3)}`;
}

export const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

export const PRIORITY_LABEL: Record<Priority, string> = {
  P1: "Emergent",
  P2: "Urgent",
  P3: "Semi-urgent",
  P4: "Routine",
};

/** Under-triage: gold more urgent than suggested (P1 is most urgent; none counts as P4). */
export function isUnderTriage(gold: Priority, suggested: Priority | "none"): boolean {
  const rank = (p: Priority | "none") => (p === "none" ? 3 : PRIORITIES.indexOf(p));
  return rank(gold) < rank(suggested);
}

export function fieldLabel(name: string): string {
  return (
    {
      patient_name: "Patient name",
      dob: "Date of birth",
      health_card_last4: "Health card (last 4)",
      referrer_name: "Referrer",
      referrer_billing_number: "Billing no.",
      modality: "Modality",
      body_part: "Body part",
      laterality: "Laterality",
      contrast_requested: "Contrast requested",
      clinical_indication: "Clinical indication",
      relevant_history: "Relevant history",
      allergies: "Allergies",
      egfr: "eGFR",
      egfr_date: "eGFR date",
      medications_of_note: "Medications",
      physician_marked_urgent: "Marked urgent",
    } as Record<string, string>
  )[name] ?? name;
}

/** Parse a user-typed field value into the JSON the API expects. */
export function parseFieldInput(field: string, raw: string): unknown {
  const text = raw.trim();
  if (["relevant_history", "allergies", "medications_of_note"].includes(field)) {
    return text ? text.split("\n").map((s) => s.trim()).filter(Boolean) : [];
  }
  if (text === "") return null;
  if (field === "egfr") return Number(text);
  if (field === "contrast_requested" || field === "physician_marked_urgent") {
    return ["yes", "true", "y", "1"].includes(text.toLowerCase());
  }
  return text;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join("\n") : "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
