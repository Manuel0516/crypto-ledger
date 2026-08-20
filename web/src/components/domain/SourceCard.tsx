import { Clock3, Settings2 } from "lucide-react";
import type { Account } from "../../types";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { StatusPill } from "./StatusPill";
import { connectorSummary, connectorTypeMeta } from "../../features/accounts/connectorTypes";
import { relativeTime, cx } from "../../lib/format";

const kindMeta: Record<string, { label: string; glyph: string }> = {
  exchange: { label: "Exchange", glyph: "BG" },
  wallet: { label: "Wallet", glyph: "W" },
  manual: { label: "Manual", glyph: "M" },
  other: { label: "Account", glyph: "•" },
};

export function accountKindLabel(kind: string): string {
  return kindMeta[kind]?.label ?? kind;
}

export function SourceCard({ account, onManage }: { account: Account; onManage: (account: Account) => void }) {
  const meta = kindMeta[account.kind] ?? kindMeta.other;
  const connector = connectorTypeMeta(account.connector_type);
  const summary = connectorSummary(account);
  return (
    <Card className="group flex flex-col">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <span
            className={cx(
              "grid size-10 shrink-0 place-items-center rounded-xl text-sm font-bold",
              account.kind === "exchange" ? "bg-accent text-white" : "bg-accent-soft text-accent",
            )}
          >
            {account.kind === "wallet" ? connector.glyph : meta.glyph}
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-ink">{account.name}</h3>
            <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-faint">
              {account.kind === "wallet" ? connector.label : meta.label}
            </p>
          </div>
        </div>
        <StatusPill status={account.paused ? "paused" : account.status} />
      </div>

      <div className="mt-4 space-y-1.5 text-xs text-soft">
        {summary && <p className="truncate font-mono text-[11px] text-faint">{summary}</p>}
        {account.wallet_software && (
          <p>
            <span className="text-faint">Software</span> · {account.wallet_software}
          </p>
        )}
        <p className="text-faint">{account.note || "No source note recorded."}</p>
      </div>

      <div className="mt-5 flex items-center justify-between border-t border-line pt-3.5">
        <span className="inline-flex items-center gap-1.5 text-[11px] text-soft">
          <Clock3 size={12} />
          {account.last_sync ? `Synced ${relativeTime(account.last_sync)}` : "Never synced"}
        </span>
        <Button size="sm" variant="ghost" icon={<Settings2 size={13} />} onClick={() => onManage(account)}>
          Manage
        </Button>
      </div>
    </Card>
  );
}