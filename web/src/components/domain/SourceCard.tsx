import {
  ExchangeBinance,
  ExchangeBitget,
  NetworkBinanceSmartChain,
  NetworkBitcoin,
  NetworkEthereum,
  NetworkPolygon,
  NetworkSolana,
  WalletLedger,
  WalletMetamask,
  WalletPhantom,
  WalletRabby,
} from "@web3icons/react";
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

function SourceIcon({ account, connector, meta }: { account: Account; connector: ReturnType<typeof connectorTypeMeta>; meta: { glyph: string } }) {
  const fallback = (
    <span className="grid size-full place-items-center text-sm font-bold">
      {account.kind === "wallet" ? connector.glyph : meta.glyph}
    </span>
  );
  const iconProps = { size: "100%", variant: "background" as const, className: "size-full" };

  if (account.kind === "exchange") {
    const exchangeName = account.connector_type === "bitget_live" ? "bitget" : account.connector_type === "binance_live" ? "binance" : account.name.trim().toLowerCase();
    const Exchange = exchangeName === "bitget" ? ExchangeBitget : exchangeName === "binance" ? ExchangeBinance : null;
    if (Exchange) return <Exchange {...iconProps} />;
  }

  if (account.kind === "wallet" && account.wallet_software) {
    const walletName = account.wallet_software.toLowerCase().replace(/[\s_-]/g, "");
    const Wallet = walletName === "metamask" ? WalletMetamask : walletName === "rabby" ? WalletRabby : walletName === "phantom" ? WalletPhantom : walletName === "ledger" ? WalletLedger : null;
    if (Wallet) return <Wallet {...iconProps} />;
  }

  if (account.kind === "wallet") {
    const networkName = (account.chain_network ?? (account.connector_type === "bitcoin_address" ? "bitcoin" : account.connector_type === "solana_address" ? "solana" : null))?.toLowerCase().replace(/[\s_-]/g, "");
    const Network = networkName === "ethereum" ? NetworkEthereum : networkName === "polygon" ? NetworkPolygon : networkName === "bsc" || networkName === "bnbsmartchain" ? NetworkBinanceSmartChain : networkName === "bitcoin" ? NetworkBitcoin : networkName === "solana" ? NetworkSolana : null;
    if (Network) return <Network {...iconProps} />;
  }

  return fallback;
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
              "grid size-10 shrink-0 place-items-center overflow-hidden rounded-xl text-accent",
              account.kind === "exchange" ? "bg-accent text-white" : "bg-accent-soft",
            )}
          >
            <SourceIcon account={account} connector={connector} meta={meta} />
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

      {account.balances?.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="text-[10px] font-medium uppercase tracking-wider text-faint">Current balance</p>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink">
            {account.balances.slice(0, 4).map((balance) => (
              <span key={`${balance.symbol}-${balance.network ?? ""}-${balance.contract_address ?? ""}`}>
                {balance.wallet_label}: {balance.amount} {balance.symbol}
              </span>
            ))}
            {account.balances.length > 4 && <span className="text-soft">+{account.balances.length - 4} more</span>}
          </div>
        </div>
      )}

      {account.fees?.length > 0 && (
        <p className="mt-2 text-[11px] text-soft">
          Fees recorded · {account.fees.slice(0, 2).map((fee) => `${fee.amount} ${fee.symbol}`).join(" · ")}
        </p>
      )}

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
