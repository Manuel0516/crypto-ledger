import { formatMoney, cx } from "../../lib/format";

export type Currency = string;

interface MoneyValueProps {
  value: number | string | null | undefined;
  currency?: Currency;
  className?: string;
  mono?: boolean;
  compact?: boolean;
}

export function MoneyValue({ value, currency = "EUR", className, mono = true, compact = false }: MoneyValueProps) {
  return (
    <span className={cx(mono && "font-mono tabular", className)}>{formatMoney(value, currency, { compact })}</span>
  );
}
