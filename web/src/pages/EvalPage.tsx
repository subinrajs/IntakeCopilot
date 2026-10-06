import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, CheckCircle2, Minus, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, post } from "../api/client";
import { keys, useEvalRun, useEvalRuns } from "../api/hooks";
import type { Confusion, EvalRun, MetricGroup, PerCase, Rate, User } from "../api/types";
import { Button, Card, ErrorBox, PriorityBadge, SimulationBanner, Spinner, cx } from "../components/ui";
import { PRIORITIES, isUnderTriage, pct, usd } from "../lib/format";

type GroupKey = "all" | "clean" | "hard";

/** Higher-is-better unless listed here. */
const LOWER_IS_BETTER = new Set(["under_triage", "model_under_triage", "over_triage"]);

const TILES: { key: keyof MetricGroup; label: string; target?: string }[] = [
  { key: "field_accuracy", label: "Field accuracy", target: "≥ 95% on clean" },
  { key: "priority_agreement", label: "Priority agreement", target: "≥ 85%" },
  { key: "under_triage", label: "Under-triage", target: "< 3%" },
  { key: "model_under_triage", label: "Under-triage, model only" },
  { key: "retrieval_recall_at_5", label: "Retrieval recall@5", target: "≥ 98%" },
  { key: "protocol_top1", label: "Protocol top-1", target: "≥ 80%" },
  { key: "contrast_flag_accuracy", label: "Contrast flag accuracy", target: "100%" },
  { key: "evidence_validity", label: "Evidence validity" },
];

function runLabel(run: EvalRun): string {
  const versions = Object.entries(run.prompt_versions)
    .map(([step, v]) => `${step} ${v}`)
    .join(", ");
  return run.label ? `${run.label} (${versions})` : versions;
}

export function EvalPage({ user }: { user: User }) {
  const runs = useEvalRuns();
  const done = (runs.data ?? []).filter((r) => r.status === "done" && r.metrics);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const selectedSummary = done.find((r) => r.id === selectedId) ?? done[0];
  const selected = useEvalRun(selectedSummary?.id);
  const baselineId = selectedSummary?.metrics?.baseline_run_id ?? undefined;
  const baseline = done.find((r) => r.id === baselineId);
  const [group, setGroup] = useState<GroupKey>("all");

  if (runs.isPending) return <Spinner />;
  if (runs.isError) return <ErrorBox error={runs.error} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">Evaluation</h1>
          <p className="max-w-3xl text-sm text-ink-2">
            Every prompt version is replayed over the frozen gold set (60 synthetic requisitions:
            40 clean, 20 deliberately hard) through the same pipeline code. Rates show counts and
            95% intervals: with 60 cases one miss moves a rate by 1.7 points.
          </p>
        </div>
        {done.length > 0 && (
          <label className="text-sm">
            <span className="mr-2 text-ink-2">Run</span>
            <select
              className="rounded-md border border-line bg-surface px-2 py-1.5"
              value={selectedSummary?.id}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              {done.map((r) => (
                <option key={r.id} value={r.id}>
                  {new Date(r.started_at).toLocaleDateString()} · {runLabel(r)}
                  {r.metrics?.simulation ? " · simulation" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {user.role === "admin" && <StartRun />}
      <RunningRuns runs={runs.data ?? []} />

      {!selectedSummary ? (
        <p className="rounded-lg border border-line bg-surface p-8 text-center text-ink-2">
          No completed evaluation runs yet.
        </p>
      ) : (
        <>
          {selectedSummary.metrics?.simulation && <SimulationBanner />}
          <ReleaseGate run={selectedSummary} baseline={baseline} />
          <div className="flex items-center gap-2">
            <span className="text-sm text-ink-2">Cases</span>
            <div role="tablist" className="flex gap-1 rounded-md border border-line bg-surface p-1">
              {(["all", "clean", "hard"] as GroupKey[]).map((g) => (
                <button
                  key={g}
                  role="tab"
                  aria-selected={group === g}
                  onClick={() => setGroup(g)}
                  className={cx("rounded px-3 py-1 text-sm capitalize", group === g ? "bg-surface-2 font-semibold" : "text-ink-2")}
                >
                  {g} ({selectedSummary.metrics?.[g].cases})
                </button>
              ))}
            </div>
            {baseline && (
              <span className="text-xs text-ink-2">Changes are against {runLabel(baseline)}.</span>
            )}
          </div>
          <MetricTiles current={selectedSummary.metrics![group]} previous={baseline?.metrics?.[group]} />
          <div className="grid gap-4 xl:grid-cols-2">
            <ConfusionCard metrics={selectedSummary.metrics!} />
            <VersionComparison runs={done} />
          </div>
          {selected.data?.per_case && (
            <FailingCases runId={selectedSummary.id} cases={selected.data.per_case} />
          )}
          <div className="grid gap-4 xl:grid-cols-2">
            <FieldErrors errors={selectedSummary.metrics!.field_errors} />
            <Nondeterminism run={selectedSummary} />
          </div>
        </>
      )}
    </div>
  );
}

function Delta({ metric, current, previous }: { metric: string; current: number | null; previous: number | null }) {
  if (current == null || previous == null) return null;
  const diff = (current - previous) * 100;
  if (Math.abs(diff) < 0.05) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-ink-2">
        <Minus className="size-3" aria-hidden /> no change
      </span>
    );
  }
  const better = LOWER_IS_BETTER.has(metric) ? diff < 0 : diff > 0;
  const Icon = diff > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className="inline-flex items-center gap-0.5 text-xs font-medium"
      style={{ color: better ? "var(--good-text)" : "var(--critical-text)" }}
    >
      <Icon className="size-3" aria-hidden />
      {diff > 0 ? "+" : ""}
      {diff.toFixed(1)} pts {better ? "better" : "worse"}
    </span>
  );
}

function MetricTiles({ current, previous }: { current: MetricGroup; previous?: MetricGroup }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {TILES.map(({ key, label, target }) => {
        const rate = current[key] as Rate;
        const prev = previous?.[key] as Rate | undefined;
        return (
          <div key={key} className="rounded-lg border border-line bg-surface p-3">
            <div className="text-xs text-ink-2">{label}</div>
            <div className="mt-1 text-2xl font-semibold">{pct(rate.value)}</div>
            <div className="tabular text-xs text-ink-2">
              {rate.n}/{rate.total} · 95% CI {pct(rate.ci95[0], 0)}–{pct(rate.ci95[1], 0)}
            </div>
            <div className="mt-1 flex flex-wrap items-center justify-between gap-1">
              <Delta metric={key} current={rate.value} previous={prev?.value ?? null} />
              {target && <span className="text-[11px] text-muted">target {target}</span>}
            </div>
          </div>
        );
      })}
      <div className="rounded-lg border border-line bg-surface p-3">
        <div className="text-xs text-ink-2">P1 suggested as P3/P4</div>
        <div className="mt-1 text-2xl font-semibold">{current.severe_under_triage}</div>
        <div className="text-xs text-ink-2">must be 0 (release gate)</div>
      </div>
      <div className="rounded-lg border border-line bg-surface p-3">
        <div className="text-xs text-ink-2">Cost per case</div>
        <div className="mt-1 text-2xl font-semibold">{usd(current.cost_usd_per_case)}</div>
        <div className="text-xs text-ink-2">estimate from token counts</div>
      </div>
      <div className="rounded-lg border border-line bg-surface p-3">
        <div className="text-xs text-ink-2">Latency p50</div>
        <div className="mt-1 text-2xl font-semibold">
          {current.latency_ms_p50 == null ? "–" : `${(current.latency_ms_p50 / 1000).toFixed(1)} s`}
        </div>
        <div className="text-xs text-ink-2">target &lt; 20 s · max {current.latency_ms_max == null ? "–" : `${(current.latency_ms_max / 1000).toFixed(1)} s`}</div>
      </div>
      <div className="rounded-lg border border-line bg-surface p-3">
        <div className="text-xs text-ink-2">Sent to manual entry</div>
        <div className="mt-1 text-2xl font-semibold">{current.manual_entry}</div>
        <div className="text-xs text-ink-2">of {current.cases} cases</div>
      </div>
    </div>
  );
}

function ReleaseGate({ run, baseline }: { run: EvalRun; baseline?: EvalRun }) {
  const gate = run.metrics!.release_gate;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-surface px-4 py-3">
      <span className="inline-flex items-center gap-1.5 font-semibold">
        {gate.passed ? (
          <CheckCircle2 className="size-5" style={{ color: "var(--good)" }} aria-hidden />
        ) : (
          <XCircle className="size-5" style={{ color: "var(--critical)" }} aria-hidden />
        )}
        Release gate {gate.passed ? "passed" : "failed"}
      </span>
      {gate.checks.map((c) => (
        <span key={c.check} className="inline-flex items-center gap-1 text-sm text-ink-2">
          {c.ok ? <CheckCircle2 className="size-3.5" style={{ color: "var(--good)" }} aria-hidden /> : <XCircle className="size-3.5" style={{ color: "var(--critical)" }} aria-hidden />}
          {c.check}
        </span>
      ))}
      {!baseline && <span className="text-xs text-muted">No earlier run to compare with: only the hard line applies.</span>}
    </div>
  );
}

function ConfusionCard({ metrics }: { metrics: NonNullable<EvalRun["metrics"]> }) {
  const [modelOnly, setModelOnly] = useState(false);
  const matrix: Confusion = modelOnly ? metrics.model_confusion : metrics.confusion;
  const columns = [...PRIORITIES, "none"] as const;
  const max = Math.max(1, ...PRIORITIES.flatMap((g) => columns.map((s) => matrix[g][s])));
  const shade = (n: number) => {
    if (n === 0) return "transparent";
    const steps = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
    return steps[Math.min(steps.length - 1, Math.floor((n / max) * (steps.length - 1)))];
  };
  return (
    <Card
      title="Priority: gold vs suggested"
      actions={
        <label className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={modelOnly} onChange={(e) => setModelOnly(e.target.checked)} />
          Model only (before red-flag rules)
        </label>
      }
    >
      <table className="w-full table-fixed text-sm">
        <caption className="mb-2 text-left text-xs text-ink-2">
          Rows are gold labels, columns the suggestion. Cells right of the diagonal (dashed red
          outline) are under-triage: the case was more urgent than suggested.
        </caption>
        <thead>
          <tr>
            <th className="w-20 text-left text-xs font-medium text-ink-2">Gold ↓</th>
            {columns.map((c) => (
              <th key={c} className="pb-1 text-center text-xs font-medium text-ink-2">
                {c === "none" ? "None" : c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {PRIORITIES.map((gold) => (
            <tr key={gold}>
              <th className="py-0.5 text-left">
                <PriorityBadge priority={gold} small />
              </th>
              {columns.map((s) => {
                const n = matrix[gold][s];
                const under = isUnderTriage(gold, s);
                const dark = n / max > 0.55;
                return (
                  <td key={s} className="p-0.5">
                    <div
                      title={`Gold ${gold}, suggested ${s}: ${n} case${n === 1 ? "" : "s"}${under ? " (under-triage)" : ""}`}
                      className={cx(
                        "grid h-11 place-items-center rounded tabular font-semibold",
                        under && n > 0 && "outline-2 outline-dashed outline-[var(--critical)]",
                        gold === s && "ring-1 ring-[var(--border)]",
                      )}
                      style={{ background: shade(n), color: n === 0 ? "var(--muted)" : dark ? "#fff" : "var(--ink)" }}
                    >
                      {n}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

const COMPARE: { key: keyof MetricGroup; label: string }[] = [
  { key: "priority_agreement", label: "Priority agreement" },
  { key: "under_triage", label: "Under-triage (lower is better)" },
  { key: "protocol_top1", label: "Protocol top-1" },
  { key: "field_accuracy", label: "Field accuracy" },
];

function VersionComparison({ runs }: { runs: EvalRun[] }) {
  const recent = useMemo(() => [...runs].slice(0, 6).reverse(), [runs]);
  const data = recent.map((r) => ({
    name: Object.values(r.prompt_versions).join("/") + (r.metrics?.simulation ? "*" : ""),
    full: runLabel(r),
    ...Object.fromEntries(COMPARE.map(({ key }) => [key, ((r.metrics!.all[key] as Rate).value ?? 0) * 100])),
  }));
  return (
    <Card title="Version comparison">
      {recent.length < 2 ? (
        <p className="text-sm text-ink-2">Run at least two versions to compare them here.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3">
            {COMPARE.map(({ key, label }) => (
              <figure key={key}>
                <figcaption className="mb-1 text-xs font-medium text-ink-2">{label}</figcaption>
                <ResponsiveContainer width="100%" height={130}>
                  <BarChart data={data} margin={{ top: 14, right: 4, bottom: 0, left: -24 }}>
                    <CartesianGrid vertical={false} stroke="var(--grid)" />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--muted)" }} tickLine={false} axisLine={{ stroke: "var(--grid)" }} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "var(--muted)" }} tickLine={false} axisLine={false} />
                    <Tooltip
                      cursor={{ fill: "var(--surface-2)" }}
                      contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", fontSize: 12, color: "var(--ink)" }}
                      formatter={(v) => [`${Number(v).toFixed(1)}%`, label]}
                      labelFormatter={(_, payload) => (payload?.[0]?.payload as { full?: string })?.full ?? ""}
                    />
                    <Bar dataKey={key} fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28}>
                      <LabelList
                        dataKey={key}
                        position="top"
                        formatter={(v: unknown) => `${Number(v).toFixed(0)}`}
                        style={{ fontSize: 10, fill: "var(--ink-2)" }}
                      />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </figure>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-ink-2">
            Oldest to newest, labelled extract/triage/protocol prompt versions. * marks simulation runs.
          </p>
        </>
      )}
    </Card>
  );
}

function FailingCases({ runId, cases }: { runId: string; cases: PerCase[] }) {
  const failing = cases.filter(
    (c) =>
      c.priority !== c.gold_priority ||
      c.protocol_id !== c.gold_protocol_id ||
      !c.contrast_ok ||
      c.field_errors.length > 0 ||
      c.status !== "done",
  );
  const order = (c: PerCase) =>
    (isUnderTriage(c.gold_priority, c.priority ?? "none") ? 0 : 1) * 10 + (c.difficulty === "hard" ? 0 : 1);
  return (
    <Card title={`Failing cases (${failing.length} of ${cases.length})`}>
      {failing.length === 0 ? (
        <p className="text-sm text-ink-2">Every case matched its gold labels.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="text-left text-xs text-ink-2">
              <tr>
                <th className="py-1 font-medium">Case</th>
                <th className="font-medium">Priority (gold → suggested)</th>
                <th className="font-medium">Protocol</th>
                <th className="font-medium">Contrast</th>
                <th className="font-medium">Field errors</th>
              </tr>
            </thead>
            <tbody>
              {[...failing].sort((a, b) => order(a) - order(b)).map((c) => {
                const under = isUnderTriage(c.gold_priority, c.priority ?? "none");
                return (
                  <tr key={c.case_key} className="border-t border-line align-top">
                    <td className="py-1.5">
                      <Link to={`/eval/${runId}/cases/${c.requisition_id}`} className="font-mono text-xs text-accent hover:underline">
                        {c.case_key}
                      </Link>
                      <div className="text-[11px] text-ink-2">{c.difficulty}</div>
                    </td>
                    <td className="py-1.5">
                      <span className="inline-flex items-center gap-1">
                        <PriorityBadge priority={c.gold_priority} small /> →{" "}
                        <PriorityBadge priority={c.priority} small />
                        {under && <span className="text-xs font-semibold text-critical">under-triage</span>}
                      </span>
                    </td>
                    <td className="py-1.5 font-mono text-xs">
                      {c.protocol_id === c.gold_protocol_id ? "✓" : `${c.protocol_id ?? "none"} (gold ${c.gold_protocol_id})`}
                      {!c.recall_at_5 && <div className="font-sans text-[11px] text-critical">gold not retrieved</div>}
                    </td>
                    <td className="py-1.5 text-xs">{c.contrast_ok ? "✓" : `${c.contrast_flags.join(", ") || "none"} (gold ${c.gold_contrast_flags.join(", ") || "none"})`}</td>
                    <td className="py-1.5 text-xs text-ink-2">{c.error ?? (c.field_errors.join(", ") || "—")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function FieldErrors({ errors }: { errors: Record<string, number> }) {
  const rows = Object.entries(errors).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  return (
    <Card title="Field errors by field">
      {rows.length === 0 ? (
        <p className="text-sm text-ink-2">No field errors.</p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {rows.map(([field, n]) => (
              <tr key={field} className="border-t border-line first:border-0">
                <td className="py-1 font-mono text-xs">{field}</td>
                <td className="py-1 text-right tabular">{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Nondeterminism({ run }: { run: EvalRun }) {
  const nd = run.metrics?.nondeterminism;
  return (
    <Card title="Non-determinism (repeat pass, no cache)">
      {!nd ? (
        <p className="text-sm text-ink-2">This run had no repeat pass.</p>
      ) : nd.cases.length === 0 ? (
        <p className="text-sm text-ink-2">Both passes agreed on every case.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {nd.cases.map((c) => (
            <li key={c.case_key}>
              <span className="font-mono text-xs">{c.case_key}</span>{" "}
              <span className="text-ink-2">{c.differs.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RunningRuns({ runs }: { runs: EvalRun[] }) {
  const active = runs.filter((r) => r.status !== "done");
  if (active.length === 0) return null;
  return (
    <div className="space-y-1">
      {active.map((r) => (
        <div key={r.id} className="rounded-md border border-line bg-surface px-3 py-2 text-sm">
          {r.status === "running" ? "Running" : "Failed"}: {runLabel(r)}
          {r.error && <span className="text-critical"> — {r.error}</span>}
        </div>
      ))}
    </div>
  );
}

function StartRun() {
  const client = useQueryClient();
  const prompts = useQuery({
    queryKey: ["prompt-versions"],
    queryFn: () => api<{ available: Record<string, string[]>; live: Record<string, string> }>("/eval/prompts"),
  });
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [limit, setLimit] = useState("");
  const [repeat, setRepeat] = useState(false);
  const start = useMutation({
    mutationFn: () =>
      post("/eval/runs", {
        prompts: { ...prompts.data?.live, ...choice },
        limit: limit ? Number(limit) : null,
        repeat: repeat ? 2 : 1,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.evalRuns }),
  });
  if (!prompts.data) return null;
  return (
    <details className="rounded-lg border border-line bg-surface px-4 py-2">
      <summary className="cursor-pointer text-sm font-medium">Start an evaluation run</summary>
      <div className="flex flex-wrap items-end gap-3 py-3">
        {Object.entries(prompts.data.available).map(([step, versions]) => (
          <label key={step} className="text-sm">
            <span className="block text-xs text-ink-2">{step}</span>
            <select
              className="rounded-md border border-line bg-bg px-2 py-1"
              value={choice[step] ?? prompts.data.live[step]}
              onChange={(e) => setChoice((c) => ({ ...c, [step]: e.target.value }))}
            >
              {versions.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
        ))}
        <label className="text-sm">
          <span className="block text-xs text-ink-2">Cases (blank = all 60)</span>
          <input className="w-24 rounded-md border border-line bg-bg px-2 py-1" value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="numeric" />
        </label>
        <label className="inline-flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} />
          Repeat pass (doubles cost)
        </label>
        <Button variant="primary" busy={start.isPending} onClick={() => start.mutate()}>
          Start run
        </Button>
        {start.isError && <ErrorBox error={start.error} />}
      </div>
      <p className="pb-2 text-xs text-ink-2">
        Runs call the model for every case not already cached, and count towards the demo's daily
        spend cap.
      </p>
    </details>
  );
}
