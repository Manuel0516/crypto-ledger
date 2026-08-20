import type { EventSummary } from "../types";

export interface LedgerStats {
  total: number;
  last24h: number;
  incoming: number;
  outgoing: number;
  modified: number;
  ledgerSince: string | null;
  weekly: Array<{ label: string; bucket: string; count: number }>;
}

/** Client-side ledger stats derived from the recent-events page — powers the
 * Overview hero's stat pills and 7-day activity sparkline (plan §88). */
export function deriveLedgerStats(events: EventSummary[]): LedgerStats {
  const now = Date.now();
  const dayAgo = now - 24 * 60 * 60 * 1000;
  let last24h = 0;
  let incoming = 0;
  let outgoing = 0;
  let modified = 0;
  let earliest: string | null = null;

  for (const event of events) {
    const time = new Date(event.occurred_at).getTime();
    if (time > dayAgo) last24h += 1;
    if (event.direction === "+") incoming += 1;
    else if (event.direction === "-") outgoing += 1;
    if (event.modified) modified += 1;
    if (earliest === null || time < new Date(earliest).getTime()) earliest = event.occurred_at;
  }

  const weekly: Array<{ label: string; bucket: string; count: number }> = [];
  for (let offset = 6; offset >= 0; offset -= 1) {
    const date = new Date(now - offset * 24 * 60 * 60 * 1000);
    const label = new Intl.DateTimeFormat("en-GB", { weekday: "short" }).format(date);
    weekly.push({ label, bucket: date.toDateString(), count: 0 });
  }
  for (const event of events) {
    const bucket = new Date(event.occurred_at).toDateString();
    const slot = weekly.findIndex((entry) => entry.bucket === bucket);
    if (slot !== -1) weekly[slot].count += 1;
  }

  return { total: events.length, last24h, incoming, outgoing, modified, ledgerSince: earliest, weekly };
}
