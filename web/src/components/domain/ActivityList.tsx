import { AlertTriangle, ArrowDownLeft, ArrowUpRight, ChevronRight, Link2, RefreshCw } from "lucide-react";
import type { EventSummary } from "../../types";
import { eventDirection } from "../../types";
import { CryptoAmount } from "./CryptoAmount";
import { MoneyValue } from "./MoneyValue";
import { StatusPill } from "./StatusPill";
import { EventTypeBadge } from "./EventTypeBadge";
import { formatDate, formatTime, cx } from "../../lib/format";

interface RowProps {
  event: EventSummary;
  onOpen: (id: number) => void;
}

interface CardRowProps extends RowProps {
  /** Render as a plain block regardless of viewport — used by compact
   * previews (e.g. the Overview page) that always want the airier card
   * layout instead of the dense desktop grid. */
  forceVisible?: boolean;
}

/** Desktop row. Hidden below md; replaced by ActivityCard. */
export function ActivityRow({ event, onOpen }: RowProps) {
  const dir = eventDirection(event);
  return (
    <button
      onClick={() => onOpen(event.id)}
      className="activity-grid hidden w-full overflow-hidden gap-[1.25rem] py-6 pl-5 pr-8 text-left text-sm transition-colors hover:bg-base md:grid"
    >
      <span className="text-[11px] text-faint">{formatDate(event.occurred_at)}<br />{formatTime(event.occurred_at)}</span>

      <span className="flex min-w-0 items-center gap-3">
        <span
          className={cx(
            "grid size-8 shrink-0 place-items-center rounded-lg",
            dir === "out" ? "bg-bad-soft text-bad" : "bg-good-soft text-good",
          )}
        >
          {dir === "out" ? <ArrowUpRight size={15} /> : <ArrowDownLeft size={15} />}
        </span>
        <span className="min-w-0">
          <EventTypeBadge eventType={event.event_type} />
        </span>
      </span>

      <span className="min-w-0">
        <span className="block text-[13px] font-medium text-ink">{event.asset_symbol}</span>
        {event.network && <span className="mt-0.5 block truncate text-[10px] text-faint">{event.network}</span>}
      </span>

      <CryptoAmount amount={event.primary_amount} symbol={event.asset_symbol} className="block truncate text-[13px] font-medium text-ink" />

      <span className="text-[13px] text-ink">
        <MoneyValue value={event.eur_value} />
      </span>

      <span className="text-[13px] text-soft">
        <MoneyValue value={event.sek_value} currency="SEK" />
      </span>

      <span className="truncate text-xs text-soft">{event.source_label}</span>

      <span className="flex items-center gap-1.5">
        <StatusPill status={event.status} />
        {event.has_open_issue && (
          <span className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-warn">
            <AlertTriangle size={10} /> issue
          </span>
        )}
        {event.modified && (
          <span className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-warn">
            <RefreshCw size={10} /> edited
          </span>
        )}
        {event.linked_event_count > 0 && (
          <span className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-accent"><Link2 size={10} /> {event.linked_event_count}</span>
        )}
      </span>

      <ChevronRight size={16} className="text-faint" />
    </button>
  );
}

/** Mobile card. Shown below md; the desktop grid collapses into rich cards (plan §9). */
export function ActivityCard({ event, onOpen, forceVisible = false }: CardRowProps) {
  const dir = eventDirection(event);
  return (
    <button
      onClick={() => onOpen(event.id)}
      className={cx("flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-base active:bg-base", !forceVisible && "md:hidden")}
    >
      <span
        className={cx(
          "grid size-10 shrink-0 place-items-center rounded-xl",
          dir === "out" ? "bg-bad-soft text-bad" : "bg-good-soft text-good",
        )}
      >
        {dir === "out" ? <ArrowUpRight size={16} /> : <ArrowDownLeft size={16} />}
      </span>

      <span className="min-w-0 flex-1">
        <EventTypeBadge eventType={event.event_type} />
        <span className="mt-1 block">
          <CryptoAmount amount={event.primary_amount} symbol={event.asset_symbol} className="text-[15px] font-semibold text-ink" />
        </span>
        <span className="mt-1 block text-[11px] text-soft">
          <MoneyValue value={event.eur_value} /> <span className="text-faint">·</span> <MoneyValue value={event.sek_value} currency="SEK" />
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-faint">
          {event.destination_label || event.address_to || event.source_label}
        </span>
      </span>

      <span className="flex shrink-0 flex-col items-end gap-1.5">
        <span className="text-[10px] text-faint">{formatTime(event.occurred_at)}</span>
        <StatusPill status={event.status} dot={false} />
        {event.has_open_issue && (
          <span className="font-mono text-[9px] uppercase tracking-wide text-warn">issue</span>
        )}
        {event.modified && (
          <span className="font-mono text-[9px] uppercase tracking-wide text-warn">edited</span>
        )}
        {event.linked_event_count > 0 && <span className="font-mono text-[9px] uppercase tracking-wide text-accent">linked</span>}
      </span>
    </button>
  );
}

export function ActivityTableHeader() {
  return (
    <div className="activity-grid border-b border-line bg-base/60 py-6 pl-5 pr-8 font-mono text-[10px] uppercase tracking-widest text-faint">
      <span>Date</span>
      <span>Activity</span>
      <span>Asset</span>
      <span>Amount</span>
      <span>EUR</span>
      <span>SEK</span>
      <span>Source</span>
      <span>Status</span>
      <span />
    </div>
  );
}
