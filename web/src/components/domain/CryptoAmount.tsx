import { formatCrypto, cx } from "../../lib/format";

interface CryptoAmountProps {
  amount: number | string | null | undefined;
  symbol?: string;
  className?: string;
  sign?: boolean;
}

export function CryptoAmount({ amount, symbol, className, sign = false }: CryptoAmountProps) {
  const num = typeof amount === "string" ? Number(amount) : amount;
  const prefix = sign && num != null && Number(num) > 0 ? "+" : "";
  return (
    <span className={cx("font-mono tabular", className)}>
      {prefix}
      {formatCrypto(amount, symbol)}
    </span>
  );
}