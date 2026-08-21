import { WalletCards } from "lucide-react";
import type { AccountHolding, AssetHolding } from "../../types";
import { truncateMiddle } from "../../lib/format";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { CryptoAmount } from "./CryptoAmount";
import { AssetIcon } from "./AssetIcon";
import { MoneyValue } from "./MoneyValue";

interface AccountHoldingsProps {
  accounts: AccountHolding[];
}

/** Shows the ownership boundary behind the aggregate portfolio total. */
export function AccountHoldings({ accounts }: AccountHoldingsProps) {
  if (accounts.length === 0) return null;

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {accounts.map((account) => (
        <Card key={account.id}>
          <div className="flex items-start gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
              <WalletCards size={17} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="truncate text-sm font-semibold text-ink">{account.name}</p>
                {account.balance_synced_at ? (
                  <Badge tone="success" dot>Live balance</Badge>
                ) : (
                  <Badge tone="neutral">Ledger</Badge>
                )}
              </div>
              <p className="mt-1 truncate font-mono text-[11px] text-soft">
                {account.address ? truncateMiddle(account.address, 10, 8) : account.connector_type}
                {account.chain_network ? ` · ${account.chain_network}` : ""}
              </p>
            </div>
          </div>

          {account.balances.length === 0 ? (
            <p className="mt-4 rounded-lg bg-base px-3 py-2 text-xs text-faint">No current holdings reported.</p>
          ) : (
            <div className="mt-4 divide-y divide-line rounded-lg border border-line bg-base">
              {account.balances.map((asset) => <BalanceRow key={asset.id} asset={asset} />)}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

function BalanceRow({ asset }: { asset: AssetHolding }) {
  const isNft = asset.asset_type === "NFT";
  const value = asset.value_display > 0 ? (
    <MoneyValue value={asset.value_display} currency={asset.display_currency} className="text-[11px] text-soft" />
  ) : (
    <span className="text-[10px] text-faint">No cached price</span>
  );

  return (
    <div className="flex items-center gap-2.5 px-3 py-2.5 first:rounded-t-lg last:rounded-b-lg">
      <AssetIcon symbol={asset.symbol} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-xs font-medium text-ink">{asset.name}</p>
          {isNft && <Badge tone="accent">NFT</Badge>}
          {!isNft && asset.asset_type === "TOKEN" && <Badge tone="info">Token</Badge>}
        </div>
        <p className="truncate font-mono text-[10px] text-faint">
          {asset.symbol}{asset.network ? ` · ${asset.network}` : ""}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <CryptoAmount amount={asset.amount} symbol={asset.symbol} className="block text-xs font-semibold text-ink" />
        {value}
      </div>
    </div>
  );
}
