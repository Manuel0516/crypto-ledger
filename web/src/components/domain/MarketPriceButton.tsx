import { useState } from "react";
import { Sparkles } from "lucide-react";
import { getJson } from "../../lib/api";
import { Button } from "../ui/Button";

export interface HistoricalMarketPrice {
  unit_price: string;
  total_value: string;
  method: string;
  granularity: string;
  observation_timestamp: string;
}

interface HistoricalPricesResponse {
  prices: Record<string, HistoricalMarketPrice>;
  missing: string[];
}

interface MarketPriceButtonProps {
  symbol: string;
  network?: string | null;
  amount: string | number;
  occurredAt: string;
  currencies: string[];
  onFilled: (prices: Record<string, HistoricalMarketPrice>) => void;
  label?: string;
}

export function MarketPriceButton({
  symbol,
  network,
  amount,
  occurredAt,
  currencies,
  onFilled,
  label = "Fill from market price",
}: MarketPriceButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const fill = async () => {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const timestamp = new Date(occurredAt);
      if (Number.isNaN(timestamp.getTime())) throw new Error("Enter a valid activity date and time first");
      const params = new URLSearchParams({
        symbol: symbol.trim().toUpperCase(),
        amount: String(amount),
        at: timestamp.toISOString(),
        currencies: currencies.join(","),
      });
      if (network?.trim()) params.set("network", network.trim());
      const response = await getJson<HistoricalPricesResponse>(`/api/prices/historical?${params.toString()}`);
      const found = Object.keys(response.prices);
      if (!found.length) throw new Error("No historical market price was found for this activity");
      onFilled(response.prices);
      const missing = response.missing?.length ? ` · ${response.missing.join(" and ")} unavailable` : "";
      setNotice(`Filled ${found.join(" and ")}${missing}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not retrieve the historical market price");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center gap-1.5 text-center">
      <Button
        size="sm"
        variant="secondary"
        icon={<Sparkles size={13} />}
        onClick={() => void fill()}
        loading={loading}
        disabled={!symbol.trim() || !String(amount).trim() || !occurredAt}
      >
        {label}
      </Button>
      {notice && <p className="text-[11px] text-good">{notice}</p>}
      {error && <p className="text-[11px] text-bad">{error}</p>}
    </div>
  );
}
