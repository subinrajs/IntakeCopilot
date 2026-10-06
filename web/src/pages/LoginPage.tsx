import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../api/client";
import { keys } from "../api/hooks";
import type { User } from "../api/types";
import { Button, ErrorBox } from "../components/ui";

const DEMO_USERS = [
  { id: "intake", label: "Intake staff" },
  { id: "radiologist", label: "Radiologist" },
  { id: "admin", label: "Admin" },
];

export function LoginPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [username, setUsername] = useState("radiologist");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await post<User>("/auth/login", { username, password });
      client.setQueryData(keys.me, user);
      navigate(user.role === "intake" ? "/upload" : "/queue");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-surface p-6">
        <div>
          <h1 className="text-xl font-bold">IntakeCopilot</h1>
          <p className="text-sm text-ink-2">
            Requisition intake for a fictional MRI &amp; CT clinic. All data is synthetic.
          </p>
        </div>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">Demo account</legend>
          <div className="flex gap-1">
            {DEMO_USERS.map((u) => (
              <button
                type="button"
                key={u.id}
                onClick={() => setUsername(u.id)}
                aria-pressed={username === u.id}
                className={`flex-1 rounded-md border px-2 py-1.5 text-xs ${
                  username === u.id ? "border-accent bg-surface-2 font-semibold" : "border-line"
                }`}
              >
                {u.label}
              </button>
            ))}
          </div>
        </fieldset>
        <label className="block space-y-1">
          <span className="text-sm font-medium">Username</span>
          <input
            className="w-full rounded-md border border-line bg-bg px-2.5 py-1.5"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm font-medium">Password</span>
          <input
            type="password"
            className="w-full rounded-md border border-line bg-bg px-2.5 py-1.5"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error != null && <ErrorBox error={error} />}
        <Button variant="primary" className="w-full" busy={busy} type="submit">
          Sign in
        </Button>
        <p className="text-xs text-ink-2">The demo password is shown in the README.</p>
      </form>
    </div>
  );
}
