import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ContrastOutput,
  EvidenceCheck,
  Extracted,
  PerCase,
  Priority,
  ProtocolOutput,
  TriageOutput,
} from "../api/types";
import { DocumentViewer, type Highlight } from "../components/DocumentViewer";
import { Card, ContrastBadge, ErrorBox, PriorityBadge, SimulationBanner, Spinner, cx } from "../components/ui";
import { displayValue, fieldLabel } from "../lib/format";

interface Step<O> {
  step: string;
  valid: boolean;
  error: string | null;
  model: string | null;
  prompt_version: string;
  output: O;
}

interface EvalCaseDetail {
  gold: {
    case_key: string;
    difficulty: string;
    gold_fields: Record<string, unknown>;
    gold_priority: Priority;
    gold_protocol_id: string;
    gold_contrast_flags: string[];
    notes: string | null;
    as_of: string;
  };
  steps: {
    extract?: Step<{ extraction: Record<string, Extracted<unknown> | Extracted<unknown>[]> | null; evidence: Record<string, EvidenceCheck> }>;
    triage?: Step<TriageOutput>;
    protocol?: Step<ProtocolOutput>;
    contrast?: Step<ContrastOutput>;
  };
  pages: { page_no: number; width: number; height: number; text_source: string }[];
  score: PerCase | null;
}

function predicted(raw: Extracted<unknown> | Extracted<unknown>[] | undefined): unknown {
  if (raw === undefined) return null;
  return Array.isArray(raw) ? raw.map((r) => r.value).filter(Boolean) : raw.value;
}

export function EvalCasePage() {
  const { runId = "", caseId = "" } = useParams();
  const query = useQuery({
    queryKey: ["eval-case", runId, caseId],
    queryFn: () => api<EvalCaseDetail>(`/eval/runs/${runId}/cases/${caseId}`),
  });
  const [active, setActive] = useState<string | null>(null);
  const highlights: Highlight[] = useMemo(() => {
    const evidence = query.data?.steps.extract?.output.evidence ?? {};
    return Object.entries(evidence)
      .filter(([path, c]) => c.found && (!active || path.split(".")[0] === active))
      .map(([path, c]) => ({ page: c.page, boxes: c.boxes, strong: path.split(".")[0] === active }));
  }, [query.data, active]);

  if (query.isPending) return <Spinner />;
  if (query.isError) return <ErrorBox error={query.error} />;
  const { gold, steps, pages, score } = query.data;
  const extraction = steps.extract?.output.extraction ?? null;
  const errors = new Set(score?.field_errors ?? []);
  const simulation = Object.values(steps).some((s) => s?.model === "dev-oracle");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/eval" className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
          <ArrowLeft className="size-4" /> Evaluation
        </Link>
        <h1 className="font-mono text-lg font-bold">{gold.case_key}</h1>
        <span className="rounded border border-line px-1.5 py-0.5 text-xs">{gold.difficulty}</span>
        {gold.notes && <span className="text-sm text-ink-2">{gold.notes}</span>}
      </div>
      {simulation && <SimulationBanner />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <DocumentViewer pages={pages} imageUrl={(n) => `/api/eval/cases/${caseId}/pages/${n}`} highlights={highlights} />
        <div className="space-y-4">
          <Card title="Priority">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              Gold <PriorityBadge priority={gold.gold_priority} /> · suggested{" "}
              <PriorityBadge priority={steps.triage?.output.final_priority ?? null} /> · model only{" "}
              <PriorityBadge priority={steps.triage?.output.model?.priority ?? null} small />
            </div>
            {steps.triage?.output.model?.rationale && <p className="mt-2 text-sm">{steps.triage.output.model.rationale}</p>}
            {(steps.triage?.output.rules.hits.length ?? 0) > 0 && (
              <p className="mt-1 text-xs text-ink-2">
                Rules: {steps.triage!.output.rules.hits.map((h) => `${h.level} ${h.phrase}`).join(", ")}
              </p>
            )}
          </Card>
          <Card title="Protocol">
            <p className="text-sm">
              Gold <span className="font-mono">{gold.gold_protocol_id}</span> · chosen{" "}
              <span className="font-mono">{steps.protocol?.output.choice?.protocol_id ?? "none"}</span>
            </p>
            <p className="mt-1 text-xs text-ink-2">
              Retrieved: {steps.protocol?.output.candidates.map((c) => c.id).join(", ") ?? "—"}
            </p>
            {steps.protocol?.output.choice?.rationale && <p className="mt-1 text-sm">{steps.protocol.output.choice.rationale}</p>}
          </Card>
          <Card title="Contrast" actions={<ContrastBadge result={steps.contrast?.output.result ?? null} />}>
            <p className="text-sm">
              Gold flags: {gold.gold_contrast_flags.join(", ") || "none"} · fired:{" "}
              {steps.contrast?.output.fired.map((f) => f.id).join(", ") || "none"}
            </p>
          </Card>
          <Card title="Fields: gold vs extracted">
            {steps.extract?.error && <ErrorBox error={steps.extract.error} />}
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-2">
                <tr>
                  <th className="py-1 font-medium">Field</th>
                  <th className="font-medium">Gold</th>
                  <th className="font-medium">Extracted</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(gold.gold_fields).map((field) => (
                  <tr
                    key={field}
                    className={cx("border-t border-line align-top", errors.has(field) && "bg-[color-mix(in_srgb,var(--critical)_8%,transparent)]")}
                    onMouseEnter={() => setActive(field)}
                    onMouseLeave={() => setActive(null)}
                  >
                    <td className="py-1 pr-2 text-xs text-ink-2">
                      {fieldLabel(field)}
                      {errors.has(field) && <span className="ml-1 font-semibold text-critical">mismatch</span>}
                    </td>
                    <td className="whitespace-pre-line py-1 pr-2">{displayValue(gold.gold_fields[field])}</td>
                    <td className="whitespace-pre-line py-1">{displayValue(predicted(extraction?.[field]))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>
    </div>
  );
}
