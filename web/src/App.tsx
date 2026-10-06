import { useQuery } from "@tanstack/react-query";

/** Placeholder shell until the intake queue lands (day 6). Shows API health. */
export function App() {
  const health = useQuery({
    queryKey: ["readyz"],
    queryFn: async () => {
      const res = await fetch("/api/readyz");
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      return (await res.json()) as { status: string };
    },
  });

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>IntakeCopilot</h1>
      <p>Requisition intake for Lakeshore MRI &amp; CT. Synthetic data only.</p>
      <p>
        API:{" "}
        {health.isPending ? "checking…" : health.isError ? health.error.message : health.data.status}
      </p>
    </main>
  );
}
