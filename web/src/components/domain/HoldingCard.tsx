import { TrendingDown, TrendingUp } from "lucide-react";
import type { AssetHolding, PriceHistory } from "../../types";
import { getJson } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Card } from "../ui/Card";
import { Badge } from "../ui/Badge";
import { MoneyValue } from "./MoneyValue";
import { CryptoAmount } from "./CryptoAmount";
import { AssetIcon } from "./AssetIcon";
import { PriceSparkline } from "./PriceSparkline";
import { cx } from "../../lib/format";

interface HoldingCardProps {
  asset: AssetHolding;
  allocationPct: number;
  flow24h: string | null;
}

/** Per-asset holding card with allocation bar, 24h net movement, and a live
 * price trend (plan §88). */
export function HoldingCard({ asset, allocationPct, flow24h }: HoldingCardProps) {
  const { data: history } = useData<PriceHistory>(
    () => getJson(`/api/prices/history?asset_id=${asset.id}&currency=${asset.display_currency}&days=7`),
    [asset.id, asset.display_currency],
    { onError: () => {} },
  );

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <AssetIcon symbol={asset.symbol} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-ink">{asset.name}</p>
          <p className="mt-0.5 truncate text-[11px] font-mono text-soft">
            {asset.symbol}{asset.network ? ` · ${asset.network}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {asset.asset_type === "NFT" && <Badge tone="accent">NFT</Badge>}
          {asset.asset_type === "TOKEN" && <Badge tone="info">Token</Badge>}
          <Badge tone="neutral" className="tabular">{allocationPct}%</Badge>
        </div>
      </div>

      {history && history.points.length > 1 && (
        <PriceSparkline points={history.points} changePct={history.change_pct} size="large" />
      )}

      <div className="space-y-1">
        <div className="flex items-center justify-between gap-3">
          <CryptoAmount amount={asset.amount} symbol={asset.symbol} className="text-lg font-semibold text-ink" />
          {flow24h && <FlowChange text={flow24h} />}
        </div>

        <div className="flex items-baseline justify-between gap-3">
          {asset.value_display > 0 ? (
            <MoneyValue value={asset.value_display} currency={asset.display_currency} className="text-sm text-ink" />
          ) : (
            <span className="text-xs text-faint">No cached price</span>
          )}
          {asset.value_display > 0 && (
            <MoneyValue
              value={asset.display_currency === "EUR" ? asset.value_sek : asset.value_eur}
              currency={asset.display_currency === "EUR" ? "SEK" : "EUR"}
              className="text-[11px] text-soft"
            />
          )}
        </div>
      </div>

      <div className="border-t border-line pt-3">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-base">
          <div className="h-full rounded-full bg-accent transition-all duration-300" style={{ width: `${Math.max(3, allocationPct)}%` }} />
        </div>
        <p className="mt-1 text-[10px] text-faint">Share of portfolio · latest cached prices</p>
      </div>
    </Card>
  );
}

function FlowChange({ text }: { text: string }) {
  const positive = text.startsWith("+");
  return (
    <p
      className={cx(
        "inline-flex w-fit items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        positive ? "bg-good-soft text-good" : "bg-bad-soft text-bad",
      )}
    >
      {positive ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
      <span className="font-mono">{text}</span>
      <span className="opacity-70">· 24h net</span>
    </p>
  );
}
