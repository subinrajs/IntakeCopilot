import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Download, Keyboard, Pencil, ShieldAlert, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, patch, post } from "../api/client";
import { keys, useCase, useProtocols, useQueue } from "../api/hooks";
import type {
  CaseDetail,
  Extracted,
  Priority,
  ProtocolRow,
  User,
} from "../api/types";
import { DocumentViewer, type Highlight } from "../components/DocumentViewer";
import {
  Button,
  Card,
  ConfidenceDot,
  ContrastBadge,
  ErrorBox,
  Kbd,
  PriorityBadge,
  SimulationBanner,
  Spinner,
  StatusPill,
  cx,
} from "../components/ui";
import { buildDecision, type ReviewState } from "../lib/decision";
import { PRIORITIES, PRIORITY_LABEL, displayValue, fieldLabel, parseFieldInput } from "../lib/format";

const FIELD_ORDER = [
  "patient_name",
  "dob",
  "health_card_last4",
  "modality",
  "body_part",
  "laterality",
  "contrast_requested",
  "clinical_indication",
  "relevant_history",
  "allergies",
  "medications_of_note",
  "egfr",
  "egfr_date",
  "physician_marked_urgent",
  "referrer_name",
  "referrer_billing_number",
] as const;
const REQUIRED = ["patient_name", "dob", "modality", "body_part", "clinical_indication"];
const pendingRelease = new Map<string, number>();

export function CasePage({ user }: { user: User }) {
  const { id = "" } = useParams();
  const query = useCase(id);
  const client = useQueryClient();
  const navigate = useNavigate();
  const claimed = useRef(false);

  const detail = query.data;
  const isRadiologist = user.role === "radiologist";
  const holdsLock = detail?.status === "in_review" && detail.locked_by === user.id;

  // A radiologist opening a waiting case takes the soft lock; leaving the page releases it.
  useEffect(() => {
    if (!detail || !isRadiologist || claimed.current) return;
    if (detail.status === "ready_for_review" || (detail.status === "in_review" && !detail.locked_by)) {
      claimed.current = true;
      post(`/cases/${id}/open`)
        .then(() => client.invalidateQueries({ queryKey: keys.case(id) }))
        .catch(() => client.invalidateQueries({ queryKey: keys.case(id) }));
    }
  }, [detail, id, isRadiologist, client]);
  useEffect(() => {
    // Deferred so a remount (React StrictMode, fast navigation back) cancels the release.
    window.clearTimeout(pendingRelease.get(id));
    return () => {
      if (!claimed.current) return;
      pendingRelease.set(
        id,
        window.setTimeout(() => void post(`/cases/${id}/release`).catch(() => undefined), 400),
      );
    };
  }, [id]);

  if (query.isPending) return <Spinner />;
  if (query.isError) return <ErrorBox error={query.error} />;
  if (!detail) return null;

  const refresh = () => client.invalidateQueries({ queryKey: keys.case(id) });
  return (
    <CaseView
      detail={detail}
      user={user}
      holdsLock={holdsLock}
      refresh={refresh}
      onBack={() => navigate("/queue")}
    />
  );
}

function CaseView({
  detail,
  user,
  holdsLock,
  refresh,
  onBack,
}: {
  detail: CaseDetail;
  user: User;
  holdsLock: boolean;
  refresh: () => void;
  onBack: () => void;
}) {
  const [activeField, setActiveField] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const extraction = detail.extraction?.extraction ?? null;
  const evidence = detail.extraction?.evidence ?? {};
  const simulation = [detail.triage?.model, detail.protocol?.model].includes("dev-oracle");

  const highlights: Highlight[] = useMemo(() => {
    const all: Highlight[] = [];
    for (const [path, check] of Object.entries(evidence)) {
      if (!check.found) continue;
      const field = path.split(".")[0]!;
      all.push({ page: check.page, boxes: check.boxes, strong: field === activeField });
    }
    return activeField ? all.filter((h) => h.strong) : all;
  }, [evidence, activeField]);

  const editable =
    detail.status === "manual_entry" ||
    detail.status === "ready_for_review" ||
    (detail.status === "in_review" && holdsLock);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={onBack} aria-label="Back to queue">
          <ArrowLeft className="size-4" /> Queue
        </Button>
        <h1 className="text-lg font-bold">{detail.fields.patient_name ?? "Unnamed requisition"}</h1>
        <StatusPill status={detail.status} />
        {detail.locked_by && !holdsLock && (
          <span className="text-sm text-ink-2">Being reviewed by {detail.locked_by}</span>
        )}
        <span className="text-xs text-ink-2">
          {detail.original_filename} · uploaded by {detail.uploaded_by}{" "}
          {new Date(detail.uploaded_at).toLocaleString()}
        </span>
        <a
          className="ml-auto inline-flex items-center gap-1 text-sm text-accent hover:underline"
          href={`/api/cases/${detail.id}/file`}
        >
          <Download className="size-4" aria-hidden /> Original
        </a>
        <Button variant="ghost" onClick={() => setHelpOpen((v) => !v)} aria-expanded={helpOpen}>
          <Keyboard className="size-4" /> Shortcuts
        </Button>
      </div>
      {helpOpen && <ShortcutHelp />}
      {simulation && <SimulationBanner />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto">
          <DocumentViewer
            pages={detail.pages}
            imageUrl={(n) => `/api/cases/${detail.id}/pages/${n}`}
            highlights={highlights}
          />
        </div>
        <div className="space-y-4">
          {detail.status === "manual_entry" && <ManualEntryBanner detail={detail} refresh={refresh} />}
          <FieldsCard
            detail={detail}
            extraction={extraction}
            editable={editable}
            onFocusField={setActiveField}
            refresh={refresh}
          />
          {detail.status !== "manual_entry" && (
            <ReviewPanels detail={detail} user={user} holdsLock={holdsLock} refresh={refresh} />
          )}
          <HistoryCard detail={detail} />
        </div>
      </div>
    </div>
  );
}

function ShortcutHelp() {
  const rows: [string, string][] = [
    ["1 – 4", "Set priority (an override asks for a reason)"],
    ["p", "Choose protocol"],
    ["f", "Acknowledge the next contrast flag"],
    ["a", "Approve"],
    ["r", "Reject (asks for a reason)"],
    ["n", "Next case in the queue"],
    ["Esc", "Back to the queue (releases the case)"],
  ];
  return (
    <Card title="Keyboard shortcuts">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([key, text]) => (
          <div key={key} className="contents">
            <dt>
              <Kbd>{key}</Kbd>
            </dt>
            <dd className="text-ink-2">{text}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

// --------------------------------------------------------------------------- fields

function FieldsCard({
  detail,
  extraction,
  editable,
  onFocusField,
  refresh,
}: {
  detail: CaseDetail;
  extraction: Record<string, unknown> | null;
  editable: boolean;
  onFocusField: (field: string | null) => void;
  refresh: () => void;
}) {
  const corrected = new Set(detail.corrections.map((c) => c.field));
  return (
    <Card
      title="Requisition fields"
      actions={
        <span className="text-xs text-ink-2">
          Hover a field to see where it came from. Low-confidence or unverified fields are
          highlighted.
        </span>
      }
    >
      <dl className="divide-y divide-[var(--border)]">
        {FIELD_ORDER.map((field) => {
          const raw = extraction?.[field] as Extracted<unknown> | Extracted<unknown>[] | undefined;
          const items = Array.isArray(raw) ? raw : raw ? [raw] : [];
          const confidence = Array.isArray(raw)
            ? raw.some((i) => i.confidence === "low")
              ? "low"
              : raw[0]?.confidence
            : raw?.confidence;
          const quotes = items.map((i) => i.evidence?.quote).filter(Boolean) as string[];
          const value = detail.fields[field as keyof typeof detail.fields];
          const unverified = Object.entries(detail.extraction?.evidence ?? {}).some(
            ([path, check]) => path.split(".")[0] === field && !check.found,
          );
          return (
            <FieldRow
              key={field}
              field={field}
              value={value}
              confidence={confidence}
              quotes={quotes}
              flagged={confidence === "low" || unverified || (REQUIRED.includes(field) && value == null)}
              unverified={unverified}
              corrected={corrected.has(field)}
              editable={editable}
              caseId={detail.id}
              onFocus={() => onFocusField(field)}
              onBlur={() => onFocusField(null)}
              refresh={refresh}
            />
          );
        })}
      </dl>
    </Card>
  );
}

function FieldRow(props: {
  field: string;
  value: unknown;
  confidence: Extracted<unknown>["confidence"] | undefined;
  quotes: string[];
  flagged: boolean;
  unverified: boolean;
  corrected: boolean;
  editable: boolean;
  caseId: string;
  onFocus: () => void;
  onBlur: () => void;
  refresh: () => void;
}) {
  const { field, value } = props;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const save = useMutation({
    mutationFn: () => patch(`/cases/${props.caseId}/fields`, { [field]: parseFieldInput(field, draft) }),
    onSuccess: () => {
      setEditing(false);
      props.refresh();
    },
  });
  const multiline = Array.isArray(value) || field === "clinical_indication";
  return (
    <div
      className={cx(
        "grid grid-cols-[150px_minmax(0,1fr)_auto] items-start gap-3 py-2",
        props.flagged && "bg-[color-mix(in_srgb,var(--warning)_10%,transparent)]",
      )}
      onMouseEnter={props.onFocus}
      onMouseLeave={props.onBlur}
    >
      <dt className="pl-1 text-xs font-medium text-ink-2">
        {fieldLabel(field)}
        {REQUIRED.includes(field) && <span className="text-critical"> *</span>}
        <div className="mt-0.5 flex flex-wrap gap-1">
          <ConfidenceDot confidence={props.confidence} />
          {props.corrected && <span className="text-[11px] text-accent">corrected</span>}
          {props.unverified && (
            <span className="text-[11px] text-critical" title="Evidence quote not found in the document">
              unverified
            </span>
          )}
        </div>
      </dt>
      <dd className="min-w-0">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
            className="space-y-1"
          >
            {multiline ? (
              <textarea
                autoFocus
                rows={3}
                className="w-full rounded border border-line bg-bg p-1.5 text-sm"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                aria-label={fieldLabel(field)}
              />
            ) : (
              <input
                autoFocus
                className="w-full rounded border border-line bg-bg px-1.5 py-1 text-sm"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                aria-label={fieldLabel(field)}
              />
            )}
            {Array.isArray(value) && <p className="text-[11px] text-ink-2">One item per line.</p>}
            {save.isError && <ErrorBox error={save.error} />}
            <div className="flex gap-1">
              <Button type="submit" variant="primary" busy={save.isPending}>
                Save
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <>
            <div className="whitespace-pre-line text-sm">{displayValue(value)}</div>
            {props.quotes.length > 0 && (
              <div className="mt-0.5 truncate text-[11px] text-ink-2" title={props.quotes.join(" · ")}>
                “{props.quotes.join("” · “")}”
              </div>
            )}
          </>
        )}
      </dd>
      <div className="pr-1">
        {props.editable && !editing && (
          <button
            className="rounded p-1 text-ink-2 hover:bg-surface-2"
            aria-label={`Edit ${fieldLabel(field)}`}
            onClick={() => {
              setDraft(Array.isArray(value) ? value.join("\n") : value == null ? "" : String(value));
              setEditing(true);
            }}
          >
            <Pencil className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

function ManualEntryBanner({ detail, refresh }: { detail: CaseDetail; refresh: () => void }) {
  const complete = useMutation({
    mutationFn: () => post(`/cases/${detail.id}/manual-entry/complete`),
    onSuccess: refresh,
  });
  return (
    <div role="alert" className="space-y-2 rounded-lg border border-line bg-surface p-4">
      <p className="flex items-center gap-1.5 font-semibold">
        <ShieldAlert className="size-4" style={{ color: "var(--warning)" }} aria-hidden />
        Manual entry needed
      </p>
      <p className="text-sm text-ink-2">
        The requisition could not be read reliably ({detail.extraction_error ?? "unknown reason"}).
        Fill in the required fields (*) from the document, then complete entry: triage, protocol
        and contrast rules then run on what you entered.
      </p>
      {complete.isError && <ErrorBox error={complete.error} />}
      <Button variant="primary" busy={complete.isPending} onClick={() => complete.mutate()}>
        Complete manual entry
      </Button>
    </div>
  );
}

// --------------------------------------------------------------------------- review

function ReviewPanels({
  detail,
  user,
  holdsLock,
  refresh,
}: {
  detail: CaseDetail;
  user: User;
  holdsLock: boolean;
  refresh: () => void;
}) {
  const navigate = useNavigate();
  const protocols = useProtocols();
  const queue = useQueue(["ready_for_review"]);
  const triage = detail.triage?.output;
  const protocolOut = detail.protocol?.output;
  const contrast = detail.contrast?.output;
  const aiPriority = triage?.final_priority ?? null;
  const aiProtocol = protocolOut?.choice?.protocol_id ?? null;
  const flags = contrast?.fired.map((f) => f.id) ?? [];
  const decided = ["approved", "exported", "rejected"].includes(detail.status);
  const canDecide = user.role === "radiologist" && holdsLock;

  const [state, setState] = useState<ReviewState>({
    priority: aiPriority,
    priorityReason: "",
    protocol: aiProtocol,
    protocolReason: "",
    acknowledged: [],
  });
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const priorityReasonRef = useRef<HTMLInputElement>(null);
  const protocolSelectRef = useRef<HTMLSelectElement>(null);
  const rejectRef = useRef<HTMLTextAreaElement>(null);

  // Contrast results can change while reviewing (e.g. an eGFR correction re-runs the rules).
  useEffect(() => {
    setState((s) => ({ ...s, acknowledged: s.acknowledged.filter((f) => flags.includes(f)) }));
  }, [flags.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const { body, problems } = buildDecision(state, { priority: aiPriority, protocol: aiProtocol, flags });
  const nextCase = queue.data?.find((c) => c.id !== detail.id && !c.locked_by);

  const approve = useMutation({
    mutationFn: () => post(`/cases/${detail.id}/decision`, body),
    onSuccess: refresh,
  });
  const reject = useMutation({
    mutationFn: () => post(`/cases/${detail.id}/decision`, { action: "reject", reason: rejectReason }),
    onSuccess: refresh,
  });

  const choosePriority = useCallback(
    (p: Priority) => {
      setState((s) => ({ ...s, priority: p }));
      if (p !== aiPriority) setTimeout(() => priorityReasonRef.current?.focus(), 0);
    },
    [aiPriority],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        if (event.key === "Escape") target.blur();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "Escape") navigate("/queue");
      if (event.key === "n" && nextCase) navigate(`/cases/${nextCase.id}`);
      if (!canDecide || decided) return;
      if (["1", "2", "3", "4"].includes(event.key)) choosePriority(`P${event.key}` as Priority);
      if (event.key === "p") {
        event.preventDefault();
        protocolSelectRef.current?.focus();
      }
      if (event.key === "f") {
        const next = flags.find((f) => !state.acknowledged.includes(f));
        if (next) setState((s) => ({ ...s, acknowledged: [...s.acknowledged, next] }));
      }
      if (event.key === "a" && body && !approve.isPending) approve.mutate();
      if (event.key === "r") {
        event.preventDefault();
        setRejecting(true);
        setTimeout(() => rejectRef.current?.focus(), 0);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canDecide, decided, flags, state, body, approve, choosePriority, navigate, nextCase]);

  return (
    <>
      <Card
        title="Priority"
        actions={
          triage?.raised_by_rules ? (
            <span className="text-xs text-ink-2">
              Model said {triage.model?.priority}; red-flag rules raised it (more urgent wins)
            </span>
          ) : null
        }
      >
        {!detail.triage ? (
          <p className="text-sm text-ink-2">Triage has not run.</p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-ink-2">Suggested</span>
              <PriorityBadge priority={aiPriority} />
              {triage?.model && <ConfidenceDot confidence={triage.model.confidence} />}
              {!detail.triage.valid && (
                <span className="text-xs text-critical">The model gave no valid suggestion; set it.</span>
              )}
            </div>
            {triage?.model?.rationale && <p className="text-sm">{triage.model.rationale}</p>}
            {(triage?.rules.hits.length ?? 0) > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-ink-2">Red-flag rules matched</p>
                <ul className="space-y-1">
                  {triage!.rules.hits.map((hit) => (
                    <li key={hit.phrase} className="text-sm">
                      <b>{hit.level}</b> · {hit.phrase}
                      <span className="text-ink-2"> — “{hit.context}”</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(triage?.model?.evidence.length ?? 0) > 0 && (
              <ul className="space-y-0.5 text-xs text-ink-2">
                {triage!.model!.evidence.map((e, i) => (
                  <li key={i}>“{e.quote}”</li>
                ))}
              </ul>
            )}
            {!decided && canDecide && (
              <div className="space-y-2 border-t border-line pt-3">
                <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Final priority">
                  {PRIORITIES.map((p, i) => (
                    <button
                      key={p}
                      role="radio"
                      aria-checked={state.priority === p}
                      onClick={() => choosePriority(p)}
                      className={cx(
                        "rounded-md border px-2.5 py-1 text-sm",
                        state.priority === p ? "border-accent bg-surface-2 font-semibold" : "border-line",
                      )}
                    >
                      <Kbd>{i + 1}</Kbd> {p} {PRIORITY_LABEL[p]}
                      {p === aiPriority && <span className="ml-1 text-[11px] text-ink-2">(AI)</span>}
                    </button>
                  ))}
                </div>
                {state.priority !== aiPriority && (
                  <input
                    ref={priorityReasonRef}
                    className="w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
                    placeholder="Reason for overriding the suggested priority (required)"
                    value={state.priorityReason}
                    onChange={(e) => setState((s) => ({ ...s, priorityReason: e.target.value }))}
                  />
                )}
              </div>
            )}
          </div>
        )}
      </Card>

      <ProtocolCard
        detail={detail}
        protocols={protocols.data ?? []}
        state={state}
        setState={setState}
        editable={!decided && canDecide}
        selectRef={protocolSelectRef}
      />

      <Card title="Contrast safety" actions={<ContrastBadge result={contrast?.result ?? null} />}>
        {!contrast ? (
          <p className="text-sm text-ink-2">Contrast rules have not run.</p>
        ) : contrast.fired.length === 0 ? (
          <p className="text-sm text-ink-2">
            No contrast rules fired (effective contrast: {contrast.effective_contrast}, rules v
            {contrast.rules_version}).
          </p>
        ) : (
          <ul className="space-y-2">
            {contrast.fired.map((rule) => (
              <li key={rule.id} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  id={`ack-${rule.id}`}
                  className="mt-1"
                  disabled={!canDecide || decided}
                  checked={decided || state.acknowledged.includes(rule.id)}
                  onChange={(e) =>
                    setState((s) => ({
                      ...s,
                      acknowledged: e.target.checked
                        ? [...s.acknowledged, rule.id]
                        : s.acknowledged.filter((f) => f !== rule.id),
                    }))
                  }
                />
                <label htmlFor={`ack-${rule.id}`} className="text-sm">
                  <ContrastBadge result={rule.result} /> {rule.message}
                  <span className="ml-1 font-mono text-[11px] text-ink-2">{rule.id}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {decided ? (
        <DecisionSummary detail={detail} user={user} refresh={refresh} />
      ) : (
        <Card title="Decision">
          {!canDecide ? (
            <p className="text-sm text-ink-2">
              {user.role === "radiologist"
                ? detail.locked_by
                  ? `${detail.locked_by} is reviewing this case.`
                  : "Opening the case for review…"
                : "A radiologist approves, overrides or rejects every case."}
            </p>
          ) : (
            <div className="space-y-3">
              {problems.length > 0 && (
                <ul className="list-inside list-disc text-sm text-ink-2">
                  {problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {approve.isError && <ErrorBox error={approve.error} />}
              {reject.isError && <ErrorBox error={reject.error} />}
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" disabled={!body} busy={approve.isPending} onClick={() => approve.mutate()}>
                  <Check className="size-4" /> Approve <Kbd>a</Kbd>
                </Button>
                <Button variant="danger" onClick={() => setRejecting((v) => !v)}>
                  <X className="size-4" /> Reject <Kbd>r</Kbd>
                </Button>
                {nextCase && (
                  <Link to={`/cases/${nextCase.id}`} className="ml-auto self-center text-sm text-accent hover:underline">
                    Next case <Kbd>n</Kbd>
                  </Link>
                )}
              </div>
              {rejecting && (
                <form
                  className="space-y-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    reject.mutate();
                  }}
                >
                  <textarea
                    ref={rejectRef}
                    rows={2}
                    className="w-full rounded-md border border-line bg-bg p-2 text-sm"
                    placeholder="Reason, sent back to the referrer (required)"
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                  />
                  <Button type="submit" variant="danger" disabled={!rejectReason.trim()} busy={reject.isPending}>
                    Confirm rejection
                  </Button>
                </form>
              )}
            </div>
          )}
        </Card>
      )}
    </>
  );
}

function ProtocolCard({
  detail,
  protocols,
  state,
  setState,
  editable,
  selectRef,
}: {
  detail: CaseDetail;
  protocols: ProtocolRow[];
  state: ReviewState;
  setState: (fn: (s: ReviewState) => ReviewState) => void;
  editable: boolean;
  selectRef: React.RefObject<HTMLSelectElement | null>;
}) {
  const out = detail.protocol?.output;
  const choice = out?.choice;
  const [showAll, setShowAll] = useState(Boolean(out?.show_all_candidates));
  const chosen = choice ? detail.protocol_candidates[choice.protocol_id] : undefined;
  const aiProtocol = choice?.protocol_id ?? null;
  const modality = detail.fields.modality;
  const options = protocols.filter((p) => !modality || p.modality === modality);
  return (
    <Card
      title="Protocol"
      actions={choice ? <ConfidenceDot confidence={choice.confidence} /> : null}
    >
      {!detail.protocol ? (
        <p className="text-sm text-ink-2">Protocol selection has not run.</p>
      ) : (
        <div className="space-y-3">
          {choice ? (
            <div>
              <p className="font-medium">
                {chosen?.name ?? choice.protocol_id}{" "}
                <span className="font-mono text-xs text-ink-2">{choice.protocol_id}</span>
              </p>
              <p className="text-sm">{choice.rationale}</p>
            </div>
          ) : (
            <p className="text-sm text-critical">No valid protocol choice; pick one below.</p>
          )}
          <div>
            <button className="text-xs text-accent hover:underline" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Hide" : "Show"} the {out?.candidates.length ?? 0} retrieved candidates
            </button>
            {showAll && (
              <ol className="mt-2 space-y-1.5">
                {out?.candidates.map((c) => {
                  const row = detail.protocol_candidates[c.id];
                  return (
                    <li key={c.id} className="rounded border border-line p-2 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span>
                          <span className="font-medium">{c.name}</span>{" "}
                          <span className="font-mono text-[11px] text-ink-2">{c.id}</span>
                        </span>
                        {editable && (
                          <Button
                            variant="ghost"
                            onClick={() => setState((s) => ({ ...s, protocol: c.id }))}
                            aria-pressed={state.protocol === c.id}
                          >
                            {state.protocol === c.id ? "Selected" : "Use"}
                          </Button>
                        )}
                      </div>
                      {row && (
                        <p className="text-xs text-ink-2">
                          {typeof row.indications === "string" ? row.indications : row.indications.join("; ")}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
          {editable && (
            <div className="space-y-2 border-t border-line pt-3">
              <label className="block text-xs font-medium text-ink-2">
                Final protocol <Kbd>p</Kbd>
                <select
                  ref={selectRef}
                  className="mt-1 block w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm text-ink"
                  value={state.protocol ?? ""}
                  onChange={(e) => setState((s) => ({ ...s, protocol: e.target.value || null }))}
                >
                  <option value="">Choose…</option>
                  {options.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.id}){p.id === aiProtocol ? " — AI" : ""}
                    </option>
                  ))}
                </select>
              </label>
              {state.protocol !== aiProtocol && (
                <input
                  className="w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
                  placeholder="Reason for overriding the suggested protocol (required)"
                  value={state.protocolReason}
                  onChange={(e) => setState((s) => ({ ...s, protocolReason: e.target.value }))}
                />
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function DecisionSummary({ detail, user, refresh }: { detail: CaseDetail; user: User; refresh: () => void }) {
  const [hl7, setHl7] = useState<string | null>(null);
  const canExport = user.role === "radiologist" || user.role === "admin";
  const exporter = useMutation({
    mutationFn: () => api<string>(`/cases/${detail.id}/export`, { method: "POST" }),
    onSuccess: (text) => {
      setHl7(text);
      refresh();
    },
  });
  const preview = useMutation({
    mutationFn: () => api<string>(`/cases/${detail.id}/hl7`),
    onSuccess: setHl7,
  });
  return (
    <Card title="Decision">
      <ul className="space-y-1 text-sm">
        {detail.decisions.map((d) => (
          <li key={d.id}>
            <b className="capitalize">{d.kind}</b>: {d.action}
            {d.final_value && d.kind !== "case" ? ` → ${d.final_value}` : ""}
            {d.action === "override" && d.ai_value ? ` (AI: ${d.ai_value})` : ""}
            {d.reason ? <span className="text-ink-2"> — “{d.reason}”</span> : null}
            <span className="text-ink-2"> · {d.decided_by}</span>
          </li>
        ))}
      </ul>
      {canExport && detail.status !== "rejected" && (
        <div className="mt-3 space-y-2">
          <div className="flex gap-2">
            <Button onClick={() => preview.mutate()} busy={preview.isPending}>
              Preview HL7 ORM
            </Button>
            {detail.status === "approved" && (
              <Button variant="primary" onClick={() => exporter.mutate()} busy={exporter.isPending}>
                Export and close
              </Button>
            )}
          </div>
          {(exporter.isError || preview.isError) && <ErrorBox error={exporter.error ?? preview.error} />}
          {hl7 && (
            <pre className="overflow-x-auto rounded-md border border-line bg-bg p-2 font-mono text-[11px] leading-relaxed">
              {hl7}
            </pre>
          )}
        </div>
      )}
    </Card>
  );
}

function HistoryCard({ detail }: { detail: CaseDetail }) {
  const [open, setOpen] = useState(false);
  return (
    <Card
      title="Audit history"
      actions={
        <button className="text-xs text-accent hover:underline" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : `Show ${detail.history.length} events`}
        </button>
      }
    >
      {open ? (
        <ol className="space-y-1 text-xs">
          {detail.history.map((h, i) => (
            <li key={i} className="grid grid-cols-[150px_120px_1fr] gap-2">
              <span className="tabular text-ink-2">{new Date(h.at).toLocaleString()}</span>
              <span>{h.actor}</span>
              <span>
                {h.action}
                {h.detail ? <span className="text-ink-2"> {JSON.stringify(h.detail)}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-xs text-ink-2">
          Every view, correction, decision and export is recorded in an insert-only audit log.
        </p>
      )}
    </Card>
  );
}
