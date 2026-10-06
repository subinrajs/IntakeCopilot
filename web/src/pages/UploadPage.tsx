import { CheckCircle2, CircleDashed, FileUp, Loader2, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Button, ErrorBox, cx } from "../components/ui";

interface Uploaded {
  id: string;
  filename: string;
}

const STEPS = ["extract", "triage", "protocol", "contrast"] as const;
const STEP_LABEL: Record<string, string> = {
  extract: "Read requisition",
  triage: "Triage",
  protocol: "Protocol",
  contrast: "Contrast rules",
};

export function UploadPage() {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [uploaded, setUploaded] = useState<Uploaded[]>([]);
  const input = useRef<HTMLInputElement>(null);

  const send = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    const form = new FormData();
    list.forEach((f) => form.append("files", f));
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ cases: Uploaded[] }>("/requisitions", { method: "POST", body: form });
      setUploaded((prev) => [...result.cases, ...prev]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-lg font-bold">Upload requisitions</h1>
        <p className="text-sm text-ink-2">
          PDF, PNG or JPEG, up to 10 MB each. Each file becomes a case; the pipeline reads it,
          suggests priority and protocol, runs the contrast rules and puts it in the review
          queue. Nothing is decided until a radiologist approves it.
        </p>
      </div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void send(e.dataTransfer.files);
        }}
        className={cx(
          "grid place-items-center gap-3 rounded-lg border-2 border-dashed p-10 text-center",
          dragging ? "border-accent bg-surface-2" : "border-line bg-surface",
        )}
      >
        <FileUp className="size-8 text-ink-2" aria-hidden />
        <p className="text-sm">Drop requisitions here, or</p>
        <Button variant="primary" busy={busy} onClick={() => input.current?.click()}>
          Choose files
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          accept="application/pdf,image/png,image/jpeg"
          className="hidden"
          data-testid="file-input"
          onChange={(e) => {
            if (e.target.files) void send(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {error != null && <ErrorBox error={error} />}
      {uploaded.length > 0 && (
        <ul className="space-y-2">
          {uploaded.map((u) => (
            <UploadProgress key={u.id} upload={u} />
          ))}
        </ul>
      )}
    </div>
  );
}

function UploadProgress({ upload }: { upload: Uploaded }) {
  const [done, setDone] = useState<string[]>([]);
  const [status, setStatus] = useState("uploaded");
  useEffect(() => {
    const source = new EventSource(`/api/requisitions/${upload.id}/events`, { withCredentials: true });
    source.addEventListener("change", (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as {
        table: string;
        detail: string | null;
      };
      if (event.table === "pipeline_steps" && event.detail) {
        setDone((prev) => (prev.includes(event.detail!) ? prev : [...prev, event.detail!]));
      }
      if (event.table === "requisitions" && event.detail) setStatus(event.detail);
    });
    return () => source.close();
  }, [upload.id]);

  const finished = ["ready_for_review", "manual_entry"].includes(status);
  return (
    <li className="rounded-lg border border-line bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{upload.filename}</span>
        {finished ? (
          <Link to={`/cases/${upload.id}`} className="text-sm text-accent hover:underline">
            {status === "manual_entry" ? "Needs manual entry →" : "Ready for review →"}
          </Link>
        ) : (
          <span className="text-xs text-ink-2">{status === "uploaded" ? "Queued" : "Processing"}</span>
        )}
      </div>
      <ol className="mt-2 flex flex-wrap gap-3 text-xs">
        {STEPS.map((step) => {
          const complete = done.includes(step);
          const failed = status === "manual_entry" && step === "extract";
          const Icon = failed ? XCircle : complete ? CheckCircle2 : finished ? CircleDashed : Loader2;
          return (
            <li key={step} className="inline-flex items-center gap-1 text-ink-2">
              <Icon
                className={cx("size-3.5", !complete && !finished && "animate-spin")}
                style={{ color: failed ? "var(--critical)" : complete ? "var(--good)" : undefined }}
                aria-hidden
              />
              {STEP_LABEL[step]}
            </li>
          );
        })}
      </ol>
    </li>
  );
}
