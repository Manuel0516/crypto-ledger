import type { ConnectorType } from "../../types";

export interface ConnectorTypeMeta {
  id: ConnectorType;
  label: string;
  hint: string;
  glyph: string;
}

export const CONNECTOR_TYPES: ConnectorTypeMeta[] = [
  { id: "exchange_import", label: "Exchange", hint: "Bitget, Binance — live API key or file import", glyph: "EX" },
  { id: "bitcoin_address", label: "Bitcoin", hint: "Address or xpub/ypub/zpub — no keys", glyph: "₿" },
  { id: "evm_address", label: "EVM wallet", hint: "MetaMask, Rabby — Ethereum, L2s, Avalanche, BSC & custom networks", glyph: "Ξ" },
  { id: "solana_address", label: "Solana", hint: "Phantom — native SOL transfers", glyph: "◎" },
  { id: "manual", label: "Manual / other", hint: "No live sync — track by hand, e.g. Monero", glyph: "•" },
];

// Metadata for connector_type values only reachable via a nested mode
// toggle inside another type's form (exchange live-mode, Lightning's NWC
// vs LND-node choice) — not shown in the top-level type picker itself.
const NESTED_CONNECTOR_META: Record<string, ConnectorTypeMeta> = {
  bitget_live: { id: "bitget_live", label: "Bitget (live)", hint: "Live API key", glyph: "EX" },
  binance_live: { id: "binance_live", label: "Binance (live)", hint: "Live API key", glyph: "EX" },
  lightning_nwc: { id: "lightning_nwc", label: "Lightning (NWC)", hint: "Nostr Wallet Connect", glyph: "⚡" },
};

export const EVM_CHAINS: { id: string; label: string }[] = [
  { id: "ethereum", label: "Ethereum" },
  { id: "polygon", label: "Polygon" },
  { id: "arbitrum", label: "Arbitrum" },
  { id: "optimism", label: "Optimism" },
  { id: "base", label: "Base" },
  { id: "avalanche", label: "Avalanche C-Chain" },
  { id: "bsc", label: "BNB Smart Chain" },
  { id: "custom", label: "Other EVM network" },
];

export function connectorTypeMeta(id: string): ConnectorTypeMeta {
  return CONNECTOR_TYPES.find((c) => c.id === id) ?? NESTED_CONNECTOR_META[id] ?? CONNECTOR_TYPES[CONNECTOR_TYPES.length - 1];
}

export function connectorSummary(account: {
  connector_type: ConnectorType;
  address: string | null;
  chain_network: string | null;
  has_config?: boolean;
}): string | null {
  const { connector_type, address, chain_network } = account;
  if (connector_type === "bitcoin_address" && address) {
    const isXpub = /^(xpub|ypub|zpub)/.test(address);
    return isXpub ? `HD wallet · ${truncateMiddle(address)}` : truncateMiddle(address);
  }
  if (connector_type === "evm_address" && address) {
    const chain = EVM_CHAINS.find((c) => c.id === chain_network)?.label ?? (chain_network === "custom" ? "Other EVM network" : "Ethereum");
    return `${chain} · ${truncateMiddle(address)}`;
  }
  if (connector_type === "solana_address" && address) return truncateMiddle(address);
  if (connector_type === "monero_rpc") return "monero-wallet-rpc";
  if (connector_type === "lightning_node") return "LND node";
  if (connector_type === "lightning_nwc") return account.has_config ? "NWC connection stored" : "No NWC connection yet";
  if (connector_type === "bitget_live") return account.has_config ? "Live API key connected" : "No API key yet";
  if (connector_type === "binance_live") return account.has_config ? "Live API key connected" : "No API key yet";
  return null;
}

function truncateMiddle(value: string, head = 6, tail = 6): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}
