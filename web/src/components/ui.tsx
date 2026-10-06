import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  FlaskConical,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { CaseStatus, Confidence, Priority } from "../api/types";
import { PRIORITY_LABEL } from "../lib/format";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  className,
  busy,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: "bg-accent text-white hover:opacity-90 border-transparent",
    secondary: "bg-surface text-ink border-line hover:bg-surface-2",
    ghost: "bg-transparent text-ink-2 border-transparent hover:bg-surface-2",
    danger: "bg-surface text-critical border-line hover:bg-surface-2",
  };
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-50",
        styles[variant],
        className,
      )}
      disabled={busy || props.disabled}
      {...props}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-lg border border-line bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const PRIORITY_STYLE: Record<Priority, string> = {
  P1: "bg-[var(--critical)] text-white",
  P2: "bg-[var(--serious)] text-black",
  P3: "bg-[#9ec5f4] text-black", // fixed light step: black text stays readable in both themes
  P4: "bg-surface-2 text-ink border border-line",
};

export function PriorityBadge({ priority, small }: { priority: Priority | null; small?: boolean }) {
  if (!priority) {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold border border-dashed border-line text-ink-2">
        Not set
      </span>
    );
  }
  return (
    <span
      title={PRIORITY_LABEL[priority]}
      className={cx(
        "inline-flex items-center rounded font-bold tabular",
        small ? "px-1.5 py-0.5 text-xs" : "px-2 py-0.5 text-sm",
        PRIORITY_STYLE[priority],
      )}
    >
      {priority}
      {!small && <span className="ml-1 font-medium">{PRIORITY_LABEL[priority]}</span>}
    </span>
  );
}

const STATUS_LABEL: Record<CaseStatus, string> = {
  uploaded: "Uploaded",
  processing: "Processing",
  manual_entry: "Manual entry",
  ready_for_review: "Ready for review",
  in_review: "In review",
  approved: "Approved",
  rejected: "Rejected",
  exported: "Exported",
};

export function StatusPill({ status }: { status: CaseStatus }) {
  return (
    <span className="inline-flex items-center rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs text-ink-2">
      {STATUS_LABEL[status]}
    </span>
  );
}

/** Status colours never stand alone: always icon + label. */
export function ContrastBadge({ result }: { result: string | null }) {
  if (!result) return <span className="text-muted">—</span>;
  const map: Record<string, { icon: LucideIcon; label: string; color: string }> = {
    clear: { icon: CheckCircle2, label: "Clear", color: "var(--good)" },
    needs_labs: { icon: FlaskConical, label: "Needs labs", color: "var(--warning)" },
    needs_review: { icon: AlertOctagon, label: "Needs review", color: "var(--critical)" },
  };
  const entry = map[result] ?? { icon: AlertTriangle, label: result, color: "var(--muted)" };
  const Icon = entry.icon;
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-ink">
      <Icon className="size-4" style={{ color: entry.color }} aria-hidden />
      {entry.label}
    </span>
  );
}

export function ConfidenceDot({ confidence }: { confidence: Confidence | undefined }) {
  if (!confidence) return null;
  const color = { high: "var(--good)", medium: "var(--warning)", low: "var(--critical)" }[
    confidence
  ];
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-ink-2" title="Model confidence">
      <span className="inline-block size-2 rounded-full" style={{ background: color }} />
      {confidence}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
      {children}
    </kbd>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-ink-2" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-md border border-line bg-surface p-3 text-sm text-critical">
      <AlertTriangle className="mr-1 inline size-4" aria-hidden />
      {message}
    </div>
  );
}

export function SimulationBanner() {
  return (
    <div
      role="note"
      className="rounded-md border border-dashed border-line bg-surface-2 px-3 py-2 text-xs text-ink-2"
    >
      <FlaskConical className="mr-1 inline size-3.5" aria-hidden />
      Simulation: these outputs come from the <b>dev-oracle</b> (gold labels), not a model. They
      exercise the workflow; they are not evidence of model quality.
    </div>
  );
}
