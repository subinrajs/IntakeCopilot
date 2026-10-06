import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { CaseDetail, EvalRun, ProtocolRow, QueueItem, User } from "./types";

export const keys = {
  me: ["me"] as const,
  queue: (status: string) => ["queue", status] as const,
  case: (id: string) => ["case", id] as const,
  protocols: ["protocols"] as const,
  evalRuns: ["eval-runs"] as const,
  evalRun: (id: string) => ["eval-run", id] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => api<User>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useQueue(statuses: string[]) {
  const qs = statuses.map((s) => `status=${encodeURIComponent(s)}`).join("&");
  return useQuery({
    queryKey: keys.queue(qs),
    queryFn: () => api<{ cases: QueueItem[] }>(`/cases?${qs}`).then((r) => r.cases),
    refetchInterval: 60_000, // waiting timers; live changes arrive over SSE
  });
}

export function useCase(id: string) {
  return useQuery({ queryKey: keys.case(id), queryFn: () => api<CaseDetail>(`/cases/${id}`) });
}

export function useProtocols() {
  return useQuery({
    queryKey: keys.protocols,
    queryFn: () => api<{ protocols: ProtocolRow[] }>("/protocols").then((r) => r.protocols),
    staleTime: 300_000,
  });
}

export function useEvalRuns() {
  return useQuery({
    queryKey: keys.evalRuns,
    queryFn: () => api<{ runs: EvalRun[] }>("/eval/runs").then((r) => r.runs),
    refetchInterval: (q) =>
      q.state.data?.some((r) => r.status === "running") ? 5_000 : false,
  });
}

export function useEvalRun(id: string | undefined) {
  return useQuery({
    queryKey: keys.evalRun(id ?? ""),
    queryFn: () => api<EvalRun>(`/eval/runs/${id}`),
    enabled: Boolean(id),
  });
}
