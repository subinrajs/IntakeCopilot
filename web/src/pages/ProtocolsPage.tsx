import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { put } from "../api/client";
import { keys, useProtocols } from "../api/hooks";
import type { ProtocolRow, User } from "../api/types";
import { Button, Card, ErrorBox, Spinner } from "../components/ui";

export function ProtocolsPage({ user }: { user: User }) {
  const protocols = useProtocols();
  const [editing, setEditing] = useState<ProtocolRow | null>(null);
  if (protocols.isPending) return <Spinner />;
  if (protocols.isError) return <ErrorBox error={protocols.error} />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold">Protocol book</h1>
        <p className="max-w-3xl text-sm text-ink-2">
          Demo content. Each protocol is one retrieval document; the model may only choose among
          the five retrieved for a case. Admin edits are re-embedded at once and versioned.
        </p>
      </div>
      {editing && <EditProtocol protocol={editing} onClose={() => setEditing(null)} />}
      {(["MRI", "CT"] as const).map((modality) => (
        <Card key={modality} title={modality}>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-2">
              <tr>
                <th className="py-1 font-medium">Protocol</th>
                <th className="font-medium">Contrast</th>
                <th className="font-medium">Indications</th>
                <th className="font-medium">Slot</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {protocols.data
                .filter((p) => p.modality === modality)
                .map((p) => (
                  <tr key={p.id} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2">
                      <div className="font-medium">{p.name}</div>
                      <div className="font-mono text-[11px] text-ink-2">
                        {p.id} · v{p.version}
                      </div>
                    </td>
                    <td className="py-1.5 pr-2">{p.contrast}</td>
                    <td className="py-1.5 pr-2 text-ink-2">{(p.indications as string[]).join("; ")}</td>
                    <td className="py-1.5 pr-2 tabular">{p.slot_minutes} min</td>
                    <td className="py-1.5 text-right">
                      {user.role === "admin" && (
                        <Button variant="ghost" onClick={() => setEditing(p)}>
                          Edit
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Card>
      ))}
    </div>
  );
}

function EditProtocol({ protocol, onClose }: { protocol: ProtocolRow; onClose: () => void }) {
  const client = useQueryClient();
  const [name, setName] = useState(protocol.name);
  const [bodyPart, setBodyPart] = useState(protocol.body_part);
  const [contrast, setContrast] = useState(protocol.contrast);
  const [slot, setSlot] = useState(String(protocol.slot_minutes ?? 30));
  const [indications, setIndications] = useState((protocol.indications as string[]).join("\n"));
  const save = useMutation({
    mutationFn: () =>
      put(`/protocols/${protocol.id}`, {
        name,
        body_part: bodyPart,
        contrast,
        slot_minutes: Number(slot),
        indications: indications.split("\n").map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.protocols });
      onClose();
    },
  });
  const input = "w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm";
  return (
    <Card title={`Edit ${protocol.id}`}>
      <form
        className="grid gap-3 md:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="text-sm">
          Name
          <input className={input} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="text-sm">
          Body part
          <input className={input} value={bodyPart} onChange={(e) => setBodyPart(e.target.value)} />
        </label>
        <label className="text-sm">
          Contrast
          <select className={input} value={contrast} onChange={(e) => setContrast(e.target.value as ProtocolRow["contrast"])}>
            <option value="none">none</option>
            <option value="iv">iv</option>
            <option value="optional">optional</option>
          </select>
        </label>
        <label className="text-sm">
          Slot (minutes)
          <input className={input} value={slot} onChange={(e) => setSlot(e.target.value)} inputMode="numeric" />
        </label>
        <label className="text-sm md:col-span-2">
          Indications (one per line)
          <textarea className={input} rows={5} value={indications} onChange={(e) => setIndications(e.target.value)} />
        </label>
        {save.isError && <ErrorBox error={save.error} />}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" busy={save.isPending}>
            Save and re-embed
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}
