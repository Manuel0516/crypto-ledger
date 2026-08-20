import { cx } from "../../lib/format";

const KNOWN_SYMBOLS: Record<string, string> = {
  BTC: "₿",
  ETH: "Ξ",
  EUR: "€",
  SEK: "kr",
  USD: "$",
  USDT: "₮",
  USDC: "$",
  XMR: "✕",
  BCH: "₿",
  LTC: "Ł",
  DOGE: "Ð",
  ADA: "₳",
  SOL: "◎",
  DOT: "◉",
  LINK: "🔗",
  XRP: "✕",
};

const KNOWN_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  XMR: "#f7f7f7",
  BCH: "#8dc351",
  LTC: "#bfbbbb",
  DOGE: "#c2a633",
  ADA: "#0033ad",
  SOL: "#14f195",
  DOT: "#e6007a",
  LINK: "#2a5ada",
  XRP: "#23292f",
  USDT: "#26a17b",
  USDC: "#2775ca",
};

interface AssetIconProps {
  symbol: string;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}

const sizes = {
  sm: "size-7 text-xs rounded-lg",
  md: "size-9 text-sm rounded-xl",
  lg: "size-11 text-base rounded-xl",
  xl: "size-14 text-xl rounded-2xl",
};

export function AssetIcon({ symbol, size = "md", className }: AssetIconProps) {
  const glyph = KNOWN_SYMBOLS[symbol] ?? symbol.slice(0, 1);
  const color = KNOWN_COLORS[symbol];

  if (color) {
    return (
      <span
        className={cx("grid shrink-0 place-items-center font-bold text-white shadow-sm", sizes[size], className)}
        style={{ backgroundColor: color }}
      >
        {glyph}
      </span>
    );
  }

  return <span className={cx("grid shrink-0 place-items-center font-bold text-accent bg-accent-soft", sizes[size], className)}>{glyph}</span>;
}