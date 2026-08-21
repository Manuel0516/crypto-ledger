export type Page = "Overview" | "Linked Accounts" | "Activity" | "Reports" | "Settings";

/* Overview */
export interface AssetHolding {
  id: number;
  symbol: string;
  name: string;
  asset_type: string;
  network: string | null;
  contract_address: string | null;
  amount: number;
  value_eur: number;
  value_sek: number;
  value_display: number;
  display_currency: string;
}

export interface AccountHolding {
  id: number;
  name: string;
  connector_type: string;
  address: string | null;
  chain_network: string | null;
  status: string;
  balance_synced_at: string | null;
  balances: AssetHolding[];
}

export interface OverviewData {
  portfolio_eur: number;
  portfolio_sek: number;
  display_currency: string;
  portfolio_display: number;
  assets: AssetHolding[];
  accounts: AccountHolding[];
  issues: number;
  last_sync: string | null;
}

/* Prices */
export interface PricePoint {
  t: string;
  price: number;
}

export interface PriceHistory {
  symbol: string;
  currency: string;
  points: PricePoint[];
  change_pct: number | null;
}

/* Events */
export interface EventSummary {
  id: number;
  occurred_at: string;
  event_type: string;
  event_subtype: string | null;
  direction: string | null;
  status: string;
  primary_amount: string;
  asset_symbol: string;
  network: string | null;
  secondary_asset_symbol: string | null;
  secondary_amount: string | null;
  source_label: string;
  destination_label: string | null;
  counterparty: string | null;
  address_from: string | null;
  address_to: string | null;
  notes: string | null;
  provenance: string;
  normalizer_version: string;
  source_id: string | null;
  source_timezone: string | null;
  imported_at: string;
  account_id: number | null;
  fee_amount: string | null;
  fee_asset_symbol: string | null;
  fee_count: number;
  eur_value: string | null;
  sek_value: string | null;
  modified: boolean;
  has_open_issue: boolean;
  is_internal: boolean;
  internal_transfer: boolean;
  linked_event_count: number;
  description: string | null;
  merchant: string | null;
  tags: string[];
  evidence_reference: string | null;
  tx_hash: string | null;
  block_hash: string | null;
  contract_address: string | null;
  order_id: string | null;
  trade_id: string | null;
  deposit_id: string | null;
  withdrawal_id: string | null;
}

export interface EventListResponse {
  items: EventSummary[];
  next_cursor: string | null;
  total: number;
}

export interface EventEvidence {
  tx_hash: string | null;
  block_height: number | null;
  block_hash: string | null;
  log_index: number | null;
  contract_address: string | null;
  order_id: string | null;
  trade_id: string | null;
  deposit_id: string | null;
  withdrawal_id: string | null;
  transaction_fee_amount: string | null;
  transaction_fee_asset: string | null;
  gas_used: string | null;
  gas_price: string | null;
  transaction_input: string | null;
  transaction_nonce: number | string | null;
  transaction_index: number | string | null;
}

export interface Valuation {
  id: number;
  quote_currency: string;
  unit_price: string;
  total_value: string;
  provider: string;
  method: string;
  granularity: string;
  confidence: string;
  manual_override: boolean;
  requested_timestamp: string;
  observation_timestamp: string;
  fetched_at: string;
}

export interface FeeDetail {
  id: number;
  fee_type: string;
  asset_symbol: string;
  amount: string;
  fee_recipient: string | null;
  manual: boolean;
}

export interface OverrideDetail {
  field: string;
  old_value: string | null;
  new_value: string | null;
  changed_at: string;
  reason: string | null;
}

export interface EventDetail {
  event: EventSummary;
  valuations: Valuation[];
  fees: FeeDetail[];
  issues: Issue[];
  raw: {
    id: number;
    source_id: string;
    external_id: string;
    received_at: string;
    source_timestamp: string | null;
    source_timezone: string | null;
    source_reference: string | null;
    payload_hash: string;
    connector_version: string;
    payload: unknown;
  } | null;
  evidence: EventEvidence;
  overrides: OverrideDetail[];
  original_values: Record<string, string | null>;
  links: EventLink[];
  modified: boolean;
}

export interface EventLink {
  id: number;
  event_id: number;
  relationship_type: string;
  orientation: "incoming" | "outgoing";
  provenance: string;
  confidence: string | null;
  notes: string | null;
  created_at: string;
  event_type: string;
  occurred_at: string;
  asset_symbol: string;
  amount: string;
}

// The full set of fields the backend accepts an override for (matches
// EDITABLE_FIELDS in api/app/core/ledger/overrides.py).
export const EDITABLE_EVENT_FIELDS = [
  "event_type",
  "event_subtype",
  "source_label",
  "destination_label",
  "counterparty",
  "description",
  "merchant",
  "tags_json",
  "evidence_reference",
  "address_from",
  "address_to",
  "notes",
  "primary_amount",
  "secondary_amount",
  "occurred_at",
  "tx_hash",
  "order_id",
  "trade_id",
  "deposit_id",
  "withdrawal_id",
  "contract_address",
  "block_hash",
] as const;

export type EditableEventField = (typeof EDITABLE_EVENT_FIELDS)[number];

export interface ManualEventInput {
  event_type: string;
  event_subtype?: string | null;
  symbol: string;
  asset_network?: string | null;
  amount: string;
  secondary_symbol?: string | null;
  secondary_asset_network?: string | null;
  secondary_amount?: string | null;
  occurred_at: string;
  account_id?: number | null;
  source_label: string;
  destination_label?: string | null;
  counterparty?: string | null;
  description?: string | null;
  merchant?: string | null;
  tags?: string[];
  evidence_reference?: string | null;
  source_timezone?: string | null;
  address_from?: string | null;
  address_to?: string | null;
  notes?: string | null;
  tx_hash?: string | null;
  order_id?: string | null;
  trade_id?: string | null;
  deposit_id?: string | null;
  withdrawal_id?: string | null;
  fee_asset?: string | null;
  fee_amount?: string | null;
  fee_type?: string;
  eur_value?: string | null;
  sek_value?: string | null;
}

/* Accounts */
export type ConnectorType =
  | "manual"
  | "exchange_import"
  | "bitcoin_address"
  | "evm_address"
  | "solana_address"
  | "monero_rpc"
  | "lightning_node"
  | "lightning_nwc"
  | "bitget_live"
  | "binance_live";

export interface Account {
  id: number;
  name: string;
  kind: string;
  connector_type: ConnectorType;
  chain_network: string | null;
  address: string | null;
  has_config: boolean;
  evm_config?: {
    chain_id?: string;
    network_name?: string;
    native_symbol?: string;
    explorer_api_url?: string;
    bsc_token_contracts?: string[];
  } | null;
  syncable: boolean;
  status: string;
  wallet_software: string | null;
  note: string | null;
  last_sync: string | null;
  archived_at: string | null;
  paused: boolean;
  event_count: number;
}

export interface SyncResult {
  status: "ok" | "unavailable" | "unsupported";
  imported: number;
  skipped: number;
  message: string | null;
  reconciliation: ReconcileResult | null;
}

export interface ReconciledAsset {
  asset_symbol: string;
  asset_network: string | null;
  live_amount: string;
  computed_amount: string;
  difference: string;
  matches: boolean;
}

export interface ReconcileResult {
  status: "ok" | "unavailable" | "unsupported";
  message: string | null;
  assets: ReconciledAsset[];
}

export interface NWCPermissionsResult {
  status: "ok" | "unavailable" | "unsupported";
  methods: string[];
  extra_methods: string[];
  message: string | null;
}

export interface AppSettings {
  sync_interval_minutes: number;
  sync_enabled: boolean;
  display_currency: string;
  minimum_activity_value: number;
  minimum_activity_currency: string;
  valuation_currencies: string[];
  price_provider: string;
  price_provider_api_key_configured: boolean;
  price_timeout_seconds: number;
  backup_hour_utc: number;
  backup_verify_after_create: boolean;
  backup_retention_daily: number;
  backup_retention_weekly: number;
  backup_retention_monthly: number;
  ui_theme: "system" | "light" | "dark";
  default_timezone: string;
  evidence_retention_policy: "indefinite";
  default_country: string | null;
  default_tax_year: number | null;
  taxpayer_name: string | null;
  default_language: string | null;
  rp2_plugins: string[];
}

export interface SecretInventoryItem {
  id: string;
  label: string;
  location: string;
  configured: boolean;
  revealable: boolean;
  deletable: boolean;
  editable: boolean;
}

/* Issues */
export interface Issue {
  id: number;
  event_id: number | null;
  severity: string;
  title: string;
  detail: string;
  linkable: boolean;
  markable: boolean;
}

/* Reports */
export interface Readiness {
  events: number;
  raw_evidence: number;
  prices: number;
  unresolved_issues: number;
  ready: boolean;
}

/* Tax reporting */
export interface TaxCountry {
  code: string;
  name: string;
  currency: string;
  methods: string[];
  default_method: string;
  engine: "rp2" | "native";
}

export interface TaxLanguage {
  code: string;
  default: boolean;
}

export interface TaxReadinessIssue {
  severity: "blocking" | "warning";
  title: string;
  detail: string;
  event_id: number | null;
  suggested_event_type: string | null;
  suggested_link_event_id: number | null;
  suggested_link_confidence: "high" | "medium" | null;
}

export interface TaxReadiness {
  country: string;
  tax_year: number;
  event_count: number;
  priced_event_count: number;
  missing_price_count: number;
  manual_event_count: number;
  unresolved_issue_count: number;
  ambiguous_transfer_count: number;
  unsynced_source_count: number;
  unpriced_fee_count: number;
  missing_raw_evidence_count: number;
  ready: boolean;
  issues: TaxReadinessIssue[];
}

export interface TaxGainLossRow {
  year: number;
  asset: string;
  category: string;
  term: string | null;
  quantity: string;
  proceeds: string;
  cost_basis: string;
  gain_loss: string;
  event_ids: number[];
}

export interface TaxIncomeRow {
  year: number;
  asset: string;
  category: string;
  quantity: string;
  fiat_value: string;
  event_ids: number[];
}

export interface TaxAcquisitionRow {
  year: number;
  asset: string;
  category: string;
  quantity: string;
  cost_basis: string;
  event_ids: number[];
}

export interface TaxTransferRow {
  occurred_at: string;
  asset: string;
  quantity: string;
  from_label: string;
  to_label: string;
  event_ids: number[];
}

export interface TaxCorrectionRow {
  event_id: number;
  occurred_at: string;
  field: string;
  old_value: string;
  new_value: string;
  changed_at: string;
}

export interface TaxEventScheduleRow {
  event_id: number;
  occurred_at: string;
  event_type: string;
  asset: string;
  amount: string;
  secondary_asset: string | null;
  secondary_amount: string | null;
  counterparty: string | null;
}

export interface TaxReconciliationSummary {
  linked_transfer_count: number;
  unresolved_issue_count: number;
  manual_event_count: number;
}

export interface TaxAssetSummaryRow {
  asset: string;
  acquired_quantity: string;
  disposed_quantity: string;
  held_quantity: string;
  cost_basis_remaining: string;
}

export interface TaxCalculationSummary {
  country: string;
  tax_year: number;
  method: string;
  currency: string;
  generated_at: string;
  gain_loss_rows: TaxGainLossRow[];
  income_rows: TaxIncomeRow[];
  asset_summary: TaxAssetSummaryRow[];
  total_short_term_gain: string;
  total_long_term_gain: string;
  total_income: string;
  total_fees: string;
  warnings: string[];
  engine: "rp2" | "native";
  acquisition_rows: TaxAcquisitionRow[];
  transfer_rows: TaxTransferRow[];
  correction_rows: TaxCorrectionRow[];
  event_schedule_rows: TaxEventScheduleRow[];
  event_schedule_total: number;
  reconciliation: TaxReconciliationSummary | null;
}

export interface TaxReport {
  id: number;
  country: string;
  tax_year: number;
  method: string;
  language: string;
  generated_at: string;
  adapter_version: string;
  ledger_snapshot_hash: string;
  price_dataset_hash: string;
  output_hashes: Record<string, string>;
  status: string;
  summary: TaxCalculationSummary;
  warnings: string[];
  has_rp2_outputs: boolean;
}

/* Attachments */
export interface Attachment {
  id: number;
  event_id: number | null;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  description: string | null;
  uploaded_at: string;
}

/* Backups */
export interface Backup {
  id: number;
  created_at: string;
  size_bytes: number;
  verified: boolean;
  verified_at: string | null;
}

export interface BackupState {
  backups: Backup[];
  has_backup_today: boolean;
}

/* Event-type display metadata. The canonical set is tax-neutral (plan §11);
   presentation groups them visually without assigning tax meaning. */
export interface EventTypeMeta {
  label: string;
  kind: "trade" | "move" | "income" | "fee" | "loan" | "nft" | "lightning" | "other" | "review";
}

export const EVENT_TYPES: Record<string, EventTypeMeta> = {
  BUY: { label: "Buy", kind: "trade" },
  SELL: { label: "Sell", kind: "trade" },
  SWAP: { label: "Swap", kind: "trade" },
  DEPOSIT: { label: "Deposit", kind: "move" },
  WITHDRAWAL: { label: "Withdrawal", kind: "move" },
  TRANSFER: { label: "Transfer", kind: "move" },
  SEND: { label: "Send", kind: "move" },
  RECEIVE: { label: "Receive", kind: "move" },
  PAYMENT: { label: "Payment", kind: "move" },
  STAKING_DEPOSIT: { label: "Staking deposit", kind: "income" },
  STAKING_WITHDRAWAL: { label: "Staking withdrawal", kind: "income" },
  STAKING_REWARD: { label: "Staking reward", kind: "income" },
  INTEREST: { label: "Interest", kind: "income" },
  YIELD: { label: "Yield", kind: "income" },
  LENDING_DEPOSIT: { label: "Lending deposit", kind: "loan" },
  LENDING_WITHDRAWAL: { label: "Lending withdrawal", kind: "loan" },
  LENDING_REWARD: { label: "Lending reward", kind: "income" },
  MINING_REWARD: { label: "Mining reward", kind: "income" },
  AIRDROP: { label: "Airdrop", kind: "income" },
  CASHBACK: { label: "Cashback", kind: "income" },
  REFERRAL_REWARD: { label: "Referral reward", kind: "income" },
  INCOME: { label: "Income", kind: "income" },
  FEE: { label: "Fee", kind: "fee" },
  NETWORK_FEE: { label: "Network fee", kind: "fee" },
  GAS_FEE: { label: "Gas fee", kind: "fee" },
  EXCHANGE_FEE: { label: "Exchange fee", kind: "fee" },
  TRADING_FEE: { label: "Trading fee", kind: "fee" },
  FUNDING_FEE: { label: "Funding fee", kind: "fee" },
  MARGIN_BORROW: { label: "Margin borrow", kind: "loan" },
  MARGIN_REPAY: { label: "Margin repay", kind: "loan" },
  MARGIN_INTEREST: { label: "Margin interest", kind: "income" },
  FUTURES_OPEN: { label: "Futures open", kind: "trade" },
  FUTURES_CLOSE: { label: "Futures close", kind: "trade" },
  FUTURES_PNL: { label: "Futures P/L", kind: "trade" },
  FUNDING_PAYMENT: { label: "Funding payment", kind: "fee" },
  LIQUIDATION: { label: "Liquidation", kind: "review" },
  NFT_MINT: { label: "Mint NFT", kind: "nft" },
  NFT_BUY: { label: "Buy NFT", kind: "nft" },
  NFT_SELL: { label: "Sell NFT", kind: "nft" },
  NFT_TRANSFER: { label: "Transfer NFT", kind: "nft" },
  LP_DEPOSIT: { label: "LP deposit", kind: "other" },
  LP_WITHDRAWAL: { label: "LP withdrawal", kind: "other" },
  LP_REWARD: { label: "LP reward", kind: "income" },
  BRIDGE_OUT: { label: "Bridge out", kind: "move" },
  BRIDGE_IN: { label: "Bridge in", kind: "move" },
  LIGHTNING_SEND: { label: "Lightning send", kind: "lightning" },
  LIGHTNING_RECEIVE: { label: "Lightning receive", kind: "lightning" },
  LIGHTNING_CHANNEL_OPEN: { label: "Channel open", kind: "lightning" },
  LIGHTNING_CHANNEL_CLOSE: { label: "Channel close", kind: "lightning" },
  LIGHTNING_FEE: { label: "Lightning fee", kind: "fee" },
  TOKEN_MINT: { label: "Token mint", kind: "other" },
  TOKEN_BURN: { label: "Token burn", kind: "other" },
  GIFT_SENT: { label: "Gift sent", kind: "move" },
  GIFT_RECEIVED: { label: "Gift received", kind: "income" },
  DONATION: { label: "Donation", kind: "move" },
  LOST: { label: "Lost", kind: "review" },
  STOLEN: { label: "Stolen", kind: "review" },
  MANUAL_ADJUSTMENT: { label: "Manual", kind: "review" },
  UNKNOWN: { label: "Unknown activity", kind: "review" },
};

export function eventMeta(type: string): EventTypeMeta {
  return EVENT_TYPES[type] ?? { label: formatType(type), kind: "other" };
}

export function formatType(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

export type Direction = "in" | "out" | "neutral";

export function eventDirection(event: Pick<EventSummary, "direction" | "event_type" | "primary_amount">): Direction {
  if (event.direction === "-") return "out";
  if (event.direction === "+") return "in";
  const amount = Number(event.primary_amount);
  if (isNaN(amount) || amount >= 0) return "in";
  return "out";
}
