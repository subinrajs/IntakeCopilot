import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Card, ErrorBox, Spinner } from "../components/ui";

interface AuditEvent {
  id: number;
  at: string;
  actor: string;
  action: string;
  entity: string;
  entity_id: string;
  detail: Record<string, unknown> | null;
}

export function AuditPage() {
  const [entity, setEntity] = useState("");
  const [entityId, setEntityId] = useState("");
  const params = new URLSearchParams();
  if (entity) params.set("entity", entity);
  if (entityId) params.set("id", entityId.trim());
  const query = useQuery({
    queryKey: ["audit", entity, entityId],
    queryFn: () => api<{ events: AuditEvent[] }>(`/audit?${params}`).then((r) => r.events),
  });
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold">Audit trail</h1>
        <p className="text-sm text-ink-2">
          Insert-only: the application's database role cannot update or delete these rows. Every
          case view, correction, decision, export and sign-in is here.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <select className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm" value={entity} onChange={(e) => setEntity(e.target.value)} aria-label="Entity">
          <option value="">All entities</option>
          <option value="requisition">Cases</option>
          <option value="user">Users</option>
          <option value="protocol">Protocols</option>
          <option value="eval_run">Eval runs</option>
        </select>
        <input
          className="w-80 rounded-md border border-line bg-surface px-2 py-1.5 text-sm"
          placeholder="Entity id (e.g. a case id)"
          value={entityId}
          onChange={(e) => setEntityId(e.target.value)}
        />
      </div>
      {query.isPending && <Spinner />}
      {query.isError && <ErrorBox error={query.error} />}
      {query.data && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] text-xs">
              <thead className="text-left text-ink-2">
                <tr>
                  <th className="py-1 font-medium">When</th>
                  <th className="font-medium">Actor</th>
                  <th className="font-medium">Action</th>
                  <th className="font-medium">Entity</th>
                  <th className="font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {query.data.map((e) => (
                  <tr key={e.id} className="border-t border-line align-top">
                    <td className="py-1 pr-2 tabular text-ink-2">{new Date(e.at).toLocaleString()}</td>
                    <td className="pr-2">{e.actor}</td>
                    <td className="pr-2 font-medium">{e.action}</td>
                    <td className="pr-2 font-mono">
                      <button className="hover:underline" onClick={() => { setEntity(e.entity); setEntityId(e.entity_id); }}>
                        {e.entity}:{e.entity_id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="font-mono text-ink-2">{e.detail ? JSON.stringify(e.detail) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
