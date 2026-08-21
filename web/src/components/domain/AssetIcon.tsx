import {
  TokenADA,
  TokenBCH,
  TokenBNB,
  TokenBTC,
  TokenDOGE,
  TokenDOT,
  TokenETH,
  TokenFDUSD,
  TokenLINK,
  TokenLTC,
  TokenMATIC,
  TokenSOL,
  TokenUNI,
  TokenUSDC,
  TokenUSDT,
  TokenWBETH,
  TokenXMR,
  TokenXRP,
} from "@web3icons/react";
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

const TOKEN_ICONS = {
  ADA: TokenADA,
  BCH: TokenBCH,
  BNB: TokenBNB,
  BTC: TokenBTC,
  DOGE: TokenDOGE,
  DOT: TokenDOT,
  ETH: TokenETH,
  FDUSD: TokenFDUSD,
  LINK: TokenLINK,
  LTC: TokenLTC,
  MATIC: TokenMATIC,
  SOL: TokenSOL,
  UNI: TokenUNI,
  USDC: TokenUSDC,
  USDT: TokenUSDT,
  WBETH: TokenWBETH,
  XMR: TokenXMR,
  XRP: TokenXRP,
};

export function AssetIcon({ symbol, size = "md", className }: AssetIconProps) {
  const normalizedSymbol = symbol.trim().toUpperCase();
  const glyph = KNOWN_SYMBOLS[normalizedSymbol] ?? normalizedSymbol.slice(0, 1);
  const color = KNOWN_COLORS[normalizedSymbol];
  const fallback = (
    <span
      className={cx(
        "grid size-full place-items-center font-bold",
        color ? "text-white shadow-sm" : "bg-accent-soft text-accent",
      )}
      style={color ? { backgroundColor: color } : undefined}
    >
      {glyph}
    </span>
  );
  const Token = TOKEN_ICONS[normalizedSymbol as keyof typeof TOKEN_ICONS];
  const iconClass = cx("grid shrink-0 place-items-center overflow-hidden", sizes[size], className);

  if (Token) {
    return (
      <span className={iconClass}>
        <Token variant="background" size="100%" className="size-full" />
      </span>
    );
  }

  return <span className={cx(iconClass, !color && "bg-accent-soft")}>{fallback}</span>;
}
