import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, ErrorBox, Spinner } from "../components/ui";
import { duration, pct, usd } from "../lib/format";

interface Ops {
  steps: {
    step: string;
    runs: number;
    avg_latency_ms: number | null;
    p95_latency_ms: number | null;
    input_tokens: number | null;
    cached_tokens: number | null;
    output_tokens: number | null;
    cost_usd: string | number | null;
    invalid_rate: string | number | null;
  }[];
  waiting_by_priority: { priority: string; cases: number; oldest: string }[];
  jobs: { status: string; n: number }[];
  queue_depth: number;
  spent_today_usd: number;
  daily_spend_cap_usd: number;
  llm_backend: string;
}

export function OpsPage() {
  const query = useQuery({ queryKey: ["ops"], queryFn: () => api<Ops>("/admin/ops"), refetchInterval: 15_000 });
  if (query.isPending) return <Spinner />;
  if (query.isError) return <ErrorBox error={query.error} />;
  const ops = query.data;
  const now = Date.now();
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">Operations</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Model backend" value={ops.llm_backend} />
        <Tile label="Jobs queued or running" value={String(ops.queue_depth)} />
        <Tile label="Model spend today" value={usd(ops.spent_today_usd)} sub={`cap ${usd(ops.daily_spend_cap_usd)}`} />
        <Tile label="Failed jobs" value={String(ops.jobs.find((j) => j.status === "failed")?.n ?? 0)} />
      </div>
      <Card title="Pipeline steps, last 7 days (excluding reused steps)">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm tabular">
            <thead className="text-left text-xs text-ink-2">
              <tr>
                {["Step", "Runs", "Avg latency", "p95 latency", "Input tokens", "Cached", "Output tokens", "Cost", "Invalid"].map((h) => (
                  <th key={h} className="py-1 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ops.steps.map((s) => (
                <tr key={s.step} className="border-t border-line">
                  <td className="py-1 font-medium">{s.step}</td>
                  <td>{s.runs}</td>
                  <td>{s.avg_latency_ms == null ? "–" : `${s.avg_latency_ms} ms`}</td>
                  <td>{s.p95_latency_ms == null ? "–" : `${s.p95_latency_ms} ms`}</td>
                  <td>{s.input_tokens ?? 0}</td>
                  <td>{s.cached_tokens ?? 0}</td>
                  <td>{s.output_tokens ?? 0}</td>
                  <td>{usd(Number(s.cost_usd ?? 0))}</td>
                  <td>{pct(Number(s.invalid_rate ?? 0))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Waiting for review, by priority">
        {ops.waiting_by_priority.length === 0 ? (
          <p className="text-sm text-ink-2">Nothing waiting.</p>
        ) : (
          <table className="w-full text-sm tabular">
            <tbody>
              {ops.waiting_by_priority.map((w) => (
                <tr key={w.priority} className="border-t border-line first:border-0">
                  <td className="py-1 font-medium">{w.priority}</td>
                  <td>{w.cases} cases</td>
                  <td className="text-ink-2">oldest waiting {duration((now - new Date(w.oldest).getTime()) / 1000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-ink-2">{sub}</div>}
    </div>
  );
}
