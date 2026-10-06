import { Clock, Flag, Lock } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueue } from "../api/hooks";
import type { QueueItem } from "../api/types";
import { ContrastBadge, ErrorBox, PriorityBadge, Spinner, StatusPill, cx } from "../components/ui";
import { duration } from "../lib/format";

const TABS: { label: string; statuses: string[] }[] = [
  { label: "To review", statuses: ["ready_for_review", "in_review"] },
  { label: "Manual entry", statuses: ["manual_entry"] },
  { label: "Processing", statuses: ["uploaded", "processing"] },
  { label: "Done", statuses: ["approved", "exported", "rejected"] },
];

export function QueuePage() {
  const [tab, setTab] = useState(0);
  const navigate = useNavigate();
  const queue = useQueue(TABS[tab]!.statuses);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">Intake queue</h1>
          <p className="text-sm text-ink-2">
            Red-flag cases are pinned first, then by suggested priority, then by how long they
            have waited. A highlighted timer means the case is past its review target.
          </p>
        </div>
        <div role="tablist" className="flex gap-1 rounded-md border border-line bg-surface p-1">
          {TABS.map((t, i) => (
            <button
              key={t.label}
              role="tab"
              aria-selected={i === tab}
              onClick={() => setTab(i)}
              className={cx(
                "rounded px-3 py-1 text-sm",
                i === tab ? "bg-surface-2 font-semibold" : "text-ink-2 hover:text-ink",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {queue.isPending && <Spinner />}
      {queue.isError && <ErrorBox error={queue.error} />}
      {queue.data && queue.data.length === 0 && (
        <p className="rounded-lg border border-line bg-surface p-8 text-center text-ink-2">
          Nothing here right now.
        </p>
      )}
      {queue.data && queue.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="border-b border-line text-left text-xs text-ink-2">
              <tr>
                <th className="px-3 py-2 font-medium">Priority</th>
                <th className="px-3 py-2 font-medium">Patient</th>
                <th className="px-3 py-2 font-medium">Exam</th>
                <th className="px-3 py-2 font-medium">Red flags</th>
                <th className="px-3 py-2 font-medium">Protocol</th>
                <th className="px-3 py-2 font-medium">Contrast</th>
                <th className="px-3 py-2 font-medium">Waiting</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {queue.data.map((item) => (
                <QueueRow key={item.id} item={item} onOpen={() => navigate(`/cases/${item.id}`)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function QueueRow({ item, onOpen }: { item: QueueItem; onOpen: () => void }) {
  return (
    <tr
      className={cx(
        "cursor-pointer border-b border-line last:border-0 hover:bg-surface-2",
        item.pinned && "bg-[color-mix(in_srgb,var(--critical)_6%,transparent)]",
      )}
      onClick={onOpen}
    >
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          {item.pinned && (
            <Flag className="size-4" style={{ color: "var(--critical)" }} aria-label="Red flag" />
          )}
          <PriorityBadge priority={item.priority} small />
          {item.raised_by_rules && (
            <span className="text-[11px] text-ink-2" title={`Model suggested ${item.model_priority}`}>
              raised by rules
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2.5">
        <Link to={`/cases/${item.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
          {item.patient_name ?? item.original_filename ?? "Unnamed"}
        </Link>
      </td>
      <td className="px-3 py-2.5">
        {[item.modality, item.body_part].filter(Boolean).join(" · ") || "—"}
      </td>
      <td className="px-3 py-2.5">
        <div className="flex max-w-[260px] flex-wrap gap-1">
          {item.red_flags.map((f) => (
            <span
              key={f.phrase}
              className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-[11px]"
              title={f.context}
            >
              {f.level} · {f.phrase}
            </span>
          ))}
        </div>
      </td>
      <td className="px-3 py-2.5 font-mono text-xs">{item.protocol_id ?? "—"}</td>
      <td className="px-3 py-2.5">
        <ContrastBadge result={item.contrast_result} />
      </td>
      <td className="px-3 py-2.5 tabular">
        <span
          className={cx(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
            item.sla_breached && "bg-[var(--highlight)] font-semibold",
          )}
          title={
            item.target_seconds
              ? `Review target ${duration(item.target_seconds)}${item.sla_breached ? " (overdue)" : ""}`
              : undefined
          }
        >
          <Clock className="size-3.5" aria-hidden />
          {duration(item.waiting_seconds)}
          {item.sla_breached && <span className="sr-only">overdue</span>}
        </span>
      </td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <StatusPill status={item.status} />
          {item.locked_by && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-ink-2">
              <Lock className="size-3" aria-hidden /> {item.locked_by}
            </span>
          )}
        </div>
      </td>
    </tr>
  );
}
