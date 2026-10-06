import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export interface LiveEvent {
  table: string;
  requisition_id: string;
  op: string;
  detail: string | null;
}

/** Server-sent events from /api/events (ids only); refetch affected queries on change. */
export function useLiveEvents(path = "/api/events", onEvent?: (e: LiveEvent) => void) {
  const client = useQueryClient();
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const source = new EventSource(path, { withCredentials: true });
    source.addEventListener("ready", () => setConnected(true));
    source.addEventListener("change", (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as LiveEvent;
      void client.invalidateQueries({ queryKey: ["queue"] });
      void client.invalidateQueries({ queryKey: ["case", event.requisition_id] });
      onEvent?.(event);
    });
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [client, path, onEvent]);
  return connected;
}
