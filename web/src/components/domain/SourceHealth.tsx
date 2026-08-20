import { Clock3, Plus, RefreshCw } from "lucide-react";
import type { Account } from "../../types";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";
import { accountKindLabel } from "./SourceCard";
import { StatusPill } from "./StatusPill";
import { relativeTime, cx } from "../../lib/format";

interface SourceHealthProps {
  accounts: Account[];
  loading?: boolean;
  onManage: (account: Account) => void;
  onAdd: () => void;
}

/** Source health panel: one row per linked account with sync freshness (plan §88). */
export function SourceHealth({ accounts, loading, onManage, onAdd }: SourceHealthProps) {
  const connected = accounts.filter((account) => account.status === "connected" || account.last_sync).length;

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-widest text-faint">Source health</p>
          <h3 className="mt-0.5 text-[15px] font-semibold text-ink">Linked sources</h3>
          <p className="mt-0.5 text-xs text-soft">
            {loading ? "Loading sources…" : `${connected} of ${accounts.length} connected`}
          </p>
        </div>
        <button
          onClick={onAdd}
          aria-label="Add source"
          className="grid size-8 place-items-center rounded-lg border border-line text-soft transition-colors hover:border-line-strong hover:text-ink"
        >
          <Plus size={15} />
        </button>
      </div>

      <div className="mt-4 space-y-1">
        {loading ? (
          <>
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
          </>
        ) : accounts.length === 0 ? (
          <p className="rounded-lg bg-base px-3 py-4 text-center text-xs text-soft">No sources linked yet.</p>
        ) : (
          accounts.map((account) => {
            const fresh = account.last_sync ? Date.now() - new Date(account.last_sync).getTime() < 24 * 3600 * 1000 : false;
            return (
              <button
                key={account.id}
                onClick={() => onManage(account)}
                className={cx(
                  "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-base",
                )}
              >
                <span
                  className={cx(
                    "size-1.5 shrink-0 rounded-full",
                    account.status === "connected" ? "bg-good" : "bg-faint",
                    fresh && "animate-pulse-soft",
                  )}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">{account.name}</span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-faint">
                    <Clock3 size={10} />
                    {account.last_sync ? `Synced ${relativeTime(account.last_sync)}` : "Never synced"} · {accountKindLabel(account.kind)}
                  </span>
                </span>
                <StatusPill status={account.status} dot={false} />
              </button>
            );
          })
        )}
      </div>

      <div className="mt-3 border-t border-line pt-3">
        <p className="flex items-center gap-1.5 text-[11px] text-faint">
          <RefreshCw size={11} />
          Each connector keeps its own cursor and sync state.
        </p>
      </div>
    </Card>
  );
}