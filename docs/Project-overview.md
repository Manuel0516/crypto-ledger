# Project Overview — Private Self-Hosted Crypto Ledger

A consolidated master plan, with **Appica UI formally included as the frontend component foundation** and the architecture centered on the application's own canonical ledger rather than RP2.

---

# Master Plan — Private Self-Hosted Crypto Ledger

## Part I — Mission & Architecture

### 1. Mission

Build a **private, self-hosted cryptocurrency portfolio, ledger, evidence, and tax-reporting system** that becomes the canonical historical record of all crypto activity.

The application is not:

- an RP2 frontend;
- an exchange dashboard;
- a wallet;
- merely a tax calculator.

It is a durable personal financial ledger capable of continuously recording activity from:

```text
Centralized exchanges
Blockchain wallets
Bitcoin
EVM networks
Monero
Lightning
Staking
Earn products
DeFi
Payments
Swaps
Bridges
NFTs
Mining
Manual transactions
Future crypto technologies
```

The system must preserve enough factual evidence to reconstruct and justify the economic history later, independently of current tax rules.

The central architecture is:

```text
External financial activity
          ↓
Immutable raw evidence
          ↓
Canonical normalized ledger
          ↓
Historical EUR + SEK valuations
          ↓
Reconciliation / ownership matching
          ↓
Portfolio state
          ↓
Country + tax-year interpretation
          ↓
PDF / CSV / RP2 / future outputs
```

The fundamental principle is:

> **Record facts permanently. Interpret them for tax purposes later.**

---

### 2. Product philosophy

Despite having a sophisticated backend, the UI should remain extremely simple.

Primary navigation:

```text
Overview
Linked Accounts
Activity
Reports
```

Settings should be accessible separately rather than becoming another major workspace.

Normal usage should look like:

```text
Open app
   ↓
Everything has synchronized automatically
   ↓
See portfolio
   ↓
Resolve warnings if there are any
   ↓
Done
```

At tax time:

```text
Reports

Country: Sweden
Tax year: 2026

✓ Data complete
✓ Prices complete
✓ Transfers reconciled
✓ Sources synchronized

Generate

[ PDF ]
[ Tax CSV ]
[ Full Ledger CSV ]
[ Evidence Archive ]
```

The user should **not need to think about tax bookkeeping while using crypto normally**.

---

### 3. Architectural invariant

Every source feeds one canonical ledger.

```text
                     SOURCES
                        │
       ┌────────────────┼────────────────┐
       │                │                │
    Exchanges        Wallets         Protocols
       │                │                │
 Bitget/Binance    BTC/EVM/XMR    Lightning/DeFi
       │                │                │
       └────────────────┼────────────────┘
                        ↓
                 RAW EVIDENCE
                        ↓
                NORMALIZATION
                        ↓
                CANONICAL LEDGER
                        ↓
            ┌───────────┼────────────┐
            ↓           ↓            ↓
        Portfolio     Reports     Tax engines
                                  ├─ RP2
                                  ├─ Sweden
                                  ├─ Spain
                                  └─ future
```

Never architect:

```text
RP2 → application
```

Instead:

```text
application → optional RP2 adapter
```

The application must remain completely useful if RP2 disappears.

---

### 4. Repository

One repository.

Do not split this into unnecessary microservices or repositories.

```text
crypto-ledger/
├── web/
├── api/
├── data/
├── tests/
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

Backend:

```text
api/
└── app/
    ├── api/
    ├── core/
    │   ├── ledger/
    │   ├── assets/
    │   ├── pricing/
    │   ├── reconciliation/
    │   └── reporting/
    │
    ├── connectors/
    │   ├── bitget/
    │   ├── binance/
    │   ├── bitcoin/
    │   ├── evm/
    │   ├── monero/
    │   ├── lightning/
    │   └── manual/
    │
    ├── integrations/
    │   └── rp2/
    │
    ├── db/
    └── security/
```

---

### 5. Technology stack

#### Frontend

Standardize on:

```text
React
TypeScript
Vite
Tailwind CSS
Appica UI
```

#### Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
```

Use:

```text
uv
```

for Python dependency management.

#### Database

Start with:

```text
SQLite
```

Do not introduce PostgreSQL unless actual usage eventually requires it.

SQLite is more than adequate for a private single-user ledger containing even millions of financial events.

#### Deployment

Docker Compose:

```bash
docker compose up -d --build
```

The production deployment lives in the homelab.

---

### 6. Appica UI design system

Use **Appica UI as the primary React component foundation**.

Appica should provide polished primitives for:

```text
Navigation
Cards
Tables
Dialogs
Drawers
Dropdowns
Forms
Tabs
Badges
Tooltips
Search
Loading states
Skeletons
Status indicators
Notifications
Interactive controls
```

The objective is to get a polished, smooth UI without wasting development time rebuilding generic components.

However:

> **Appica is the component foundation, not the application's visual identity.**

Create a project-specific design system above Appica.

Frontend structure:

```text
web/src/
├── app/
├── pages/
├── features/
├── components/
│   ├── ui/
│   └── domain/
├── api/
├── hooks/
├── types/
└── lib/
```

#### `components/ui/`

Contains wrappers around Appica primitives:

```text
Button
Dialog
Drawer
Table
Input
Select
Tooltip
Card
Badge
Dropdown
Tabs
Skeleton
```

Avoid scattering direct Appica usage throughout the application when a reusable wrapper makes sense.

#### `components/domain/`

Contains crypto-specific UI:

```text
PortfolioValue
AssetBalance
AssetIcon
MoneyValue
CryptoAmount

SourceCard
SourceStatus
SyncStatus

ActivityRow
ActivityCard
EventDetailsDrawer

TransactionHash
AccountReference

PriceProvenance
EvidenceStatus
TransferMatch

IssueBanner
IssueCard

ReportReadiness
TaxSummary
BackupStatus
```

Domain components should depend on generic UI primitives, not vice versa.

---

### 7. UI philosophy

The app should feel like a polished financial application, not a generic admin dashboard.

Use:

- restrained typography;
- strong information hierarchy;
- generous but efficient spacing;
- subtle borders;
- consistent radius;
- semantic status states;
- responsive layouts;
- subtle animations;
- excellent loading states.

Avoid excessive animation.

Animations should communicate things such as:

```text
sync completed
drawer opened
transaction expanded
new activity appeared
issue resolved
report generated
```

rather than simply decorating the UI.

---

### 8. Light and dark mode

Both should be first-class.

Use CSS variables/design tokens for:

```text
background
surface
surface-elevated
text
text-muted
border
positive
negative
warning
error
information
```

Do not hardcode colors throughout components.

This allows the application's visual identity to evolve independently of Appica.

---

### 9. Responsive UI

Desktop and mobile should be **intentionally designed** and **completely responsive**.

Desktop Activity:

```text
Date | Activity | Asset | Amount | EUR | SEK | Source | Status
```

Mobile should **not** simply shrink this table.

Instead:

```text
┌─────────────────────────┐
│ ↗ Sent BTC              │
│ 0.00142 BTC             │
│                         │
│ 87.42 EUR · 974 SEK     │
│ Cake Wallet        14:32│
└─────────────────────────┘
```

Tapping opens the full event drawer.

---

### 10. Separate UI from business logic

Never put financial/accounting logic inside Appica components.

Use:

```text
API client
   ↓
hooks/services
   ↓
domain models
   ↓
domain components
   ↓
Appica primitives
```

Replacing Appica in five years should not require rewriting the ledger logic.

---

## Part II — Domain Model

### 11. Canonical event model

The core historical object is an **Event**, not simply a transaction.

Supported event types should be extensible and initially include:

```text
BUY
SELL
SWAP

DEPOSIT
WITHDRAWAL
TRANSFER
SEND
RECEIVE
PAYMENT

STAKING_DEPOSIT
STAKING_WITHDRAWAL
STAKING_REWARD

INTEREST
YIELD

LENDING_DEPOSIT
LENDING_WITHDRAWAL
LENDING_REWARD

MINING_REWARD
AIRDROP
CASHBACK
REFERRAL_REWARD
INCOME

FEE
NETWORK_FEE
GAS_FEE
EXCHANGE_FEE
TRADING_FEE
FUNDING_FEE

MARGIN_BORROW
MARGIN_REPAY
MARGIN_INTEREST

FUTURES_OPEN
FUTURES_CLOSE
FUTURES_PNL
FUNDING_PAYMENT
LIQUIDATION

NFT_MINT
NFT_BUY
NFT_SELL
NFT_TRANSFER

LP_DEPOSIT
LP_WITHDRAWAL
LP_REWARD

BRIDGE_OUT
BRIDGE_IN

LIGHTNING_SEND
LIGHTNING_RECEIVE
LIGHTNING_CHANNEL_OPEN
LIGHTNING_CHANNEL_CLOSE
LIGHTNING_FEE

TOKEN_MINT
TOKEN_BURN

GIFT_SENT
GIFT_RECEIVED
DONATION

LOST
STOLEN

MANUAL_ADJUSTMENT
UNKNOWN
```

Do not encode tax consequences into these values.

For example:

```text
PAYMENT
BTC
-0.0002 BTC
```

is a fact.

Whether that payment constitutes a taxable disposal is determined by the selected jurisdiction.

---

### 12. Event identity

Store:

```text
internal_event_id
external_event_id
source_id
raw_event_id
```

IDs should make imports idempotent.

Synchronizing Bitget twice must never create two copies of the same transaction.

---

### 13. Time information

Preserve:

```text
occurred_at_utc
original_timestamp
source_timezone
imported_at
created_at
updated_at
```

Internally use UTC.

Never discard the original source timestamp.

---

### 14. Event classification

Store:

```text
event_type
event_subtype
direction
status
```

Classification must be versionable because parsers may improve.

---

### 15. Assets and amounts

Events should support multiple economic legs.

At minimum:

```text
primary_asset
primary_amount

secondary_asset
secondary_amount
```

A swap may therefore represent:

```text
BTC
-0.001

ETH
+0.034
```

with associated fees.

More complex multi-leg activities should use linked events where appropriate.

---

### 16. Accounts

Store:

```text
source_account
destination_account
counterparty
ownership status
```

Ownership is critical.

The application must understand the difference between:

```text
Bitget → my Bitcoin wallet
```

and:

```text
my Bitcoin wallet → Mullvad
```

The first is an internal movement.

The second is a payment to an external party.

---

### 17. Network evidence

Where applicable:

```text
network
chain_id
block_height
block_hash
transaction_hash
log_index
address_from
address_to
contract_address
token_id
```

---

### 18. Exchange evidence

Where applicable:

```text
exchange
account_type
order_id
trade_id
transaction_id
deposit_id
withdrawal_id
```

---

### 19. Fees

Do not assume an event has only one fee.

Use separate fee records:

```text
fee_type
fee_asset
fee_amount
fee_recipient
```

This permits:

```text
exchange trading fee
network fee
gas
Lightning routing fee
funding fee
```

---

### 20. Metadata

Allow:

```text
description
counterparty
merchant
notes
tags
```

This becomes useful years later when explaining transactions.

---

### 21. Provenance

Every event records:

```text
automatic/manual
connector
connector_version
normalizer_version
raw_source_reference
```

The system must always know **where an event came from**.

---

### 22. Immutable raw evidence

Before normalization, preserve the original source record.

```text
raw_events
├── id
├── source_id
├── external_id
├── received_at
├── source_timestamp
├── payload_json
├── payload_hash
├── connector_version
└── immutable
```

Example:

```text
Bitget API response
       ↓
RAW EVENT
       ↓
Normalizer
       ↓
Canonical event
```

Never modify the raw event.

---

### 23. Evidence hashing

Calculate a cryptographic hash such as SHA-256 for raw records.

This allows detection of accidental or malicious modification.

Eventually generate periodic integrity manifests:

```text
integrity/
├── hashes.csv
└── manifest.json
```

This improves evidence integrity without claiming that hashes alone provide legal proof.

---

### 24. Normalizer versioning

Store the version that interpreted each raw event.

Example:

```text
raw Bitget event
       ↓
Bitget normalizer v1.3.2
       ↓
canonical event
```

If a bug is discovered:

```text
same raw event
       ↓
Bitget normalizer v1.4
       ↓
corrected interpretation
```

No historical evidence is lost.

---

### 25. Asset registry

Never identify assets only by ticker.

Use:

```text
asset_id
symbol
name
asset_type
network
contract_address
decimals
coingecko_id
```

Asset types:

```text
COIN
TOKEN
STABLECOIN
NFT
LP_TOKEN
WRAPPED_ASSET
FIAT
OTHER
```

For tokens, network + contract address is fundamental.

---

### 26. Account registry

Accounts represent actual financial ownership.

```text
id
name
type
owner
network
address
watch_reference
source_connector
wallet_software
active
created_at
archived_at
```

Types:

```text
EXCHANGE
BLOCKCHAIN_ADDRESS
BLOCKCHAIN_WALLET
LIGHTNING
MONERO
DEFI
MANUAL
OTHER
```

---

### 27. Wallet software is not ownership

MetaMask, Cake and Exodus are usually interfaces over underlying financial accounts.

For example:

```text
Ethereum address 0xABC...
wallet software = MetaMask
```

Moving that address to Rabby should not create a new financial history.

Likewise:

```text
Cake BTC
```

should ultimately map to Bitcoin accounts rather than being treated as an opaque Cake account whenever possible.

---

## Part III — Sources & Connectors

### 28. Connector interface

All data sources implement a stable abstraction conceptually similar to:

```python
class SourceConnector:
    test_connection()
    get_accounts()
    get_balances()
    sync(cursor)
    backfill(start, end)
    normalize(raw_event)
```

Flow:

```text
Connector
    ↓
Raw events
    ↓
Normalizer
    ↓
Canonical events
```

Each connector maintains independent synchronization state.

---

### 29. Centralized exchanges

Initially:

```text
Bitget
Binance
```

Future:

```text
Kraken
Coinbase
OKX
Bybit
KuCoin
anything else
```

Adding one should not require database redesign.

CEX connectors should eventually capture:

```text
fiat deposits/withdrawals
crypto deposits/withdrawals
spot trading
conversions
swaps
fees
staking
earn
interest
rewards
margin
borrowing
repayments
futures
funding
realized P/L
liquidations
cashback
referrals
airdrops
internal transfers
```

---

### 30. Bitget — first production connector

Bitget gets priority because the existing history needs to be preserved before moving assets.

Implement:

```text
historical backfill
incremental sync
pagination
stable IDs
rate-limit handling
retries
deduplication
raw-record preservation
```

Use **read-only credentials only**.

No trading permission.

No withdrawal permission.

Import as much historical account activity as the available Bitget APIs/exports permit.

---

### 31. Binance

Second centralized exchange connector.

Use exactly the same pipeline:

```text
Binance
   ↓
raw Binance records
   ↓
Binance normalizer
   ↓
canonical events
```

Support relevant Binance products progressively.

---

### 32. Bitcoin

Eventually support:

```text
addresses
xpub
descriptors
Bitcoin Core watch-only wallets
```

Record:

```text
txid
inputs
outputs
block
confirmations
fees
owned addresses
change
```

Never store:

```text
seed
xprv
private key
```

---

### 33. EVM / MetaMask

Track EVM addresses rather than relying primarily on MetaMask.

Support:

```text
ETH
ERC-20
ERC-721
ERC-1155
gas
contract interactions
DEX swaps
bridges
staking
DeFi
```

One blockchain transaction may generate several canonical events.

---

### 34. Exodus

Track underlying blockchains wherever possible.

Treat Exodus primarily as wallet-software metadata unless its exports provide additional useful evidence.

---

### 35. Cake Wallet

Handle assets independently:

```text
Cake BTC
→ Bitcoin connector

Cake EVM
→ EVM connector

Cake XMR
→ Monero connector

Cake Lightning
→ Lightning/off-chain connector

Cake swaps
→ swap/provider + wallet evidence
```

Do not pretend these technologies expose equivalent information.

---

### 36. Monero

Monero needs dedicated privacy-aware support.

Potential sources:

```text
monero-wallet-rpc
view-only configuration
wallet exports
manual imports
```

Never send Monero financial history to public explorers.

The UI must clearly indicate limitations where view-only information cannot reconstruct all outgoing activity.

---

### 37. Lightning

Design for Lightning immediately even if implementation comes later.

Do not model Lightning as an ordinary blockchain.

Support:

```text
payment_hash
invoice
amount_msat
fee_msat
channel_id
node_id
settled_at
```

Events:

```text
LIGHTNING_SEND
LIGHTNING_RECEIVE
LIGHTNING_FEE
LIGHTNING_CHANNEL_OPEN
LIGHTNING_CHANNEL_CLOSE
```

Bitcoin channel-open/close transactions must eventually reconcile with Lightning events to prevent double counting.

---

### 38. Future technologies

Support generic:

```text
protocol
network type
metadata
linked events
multi-leg transformations
```

Unknown activity may initially become:

```text
UNKNOWN
```

while retaining all raw evidence.

That is preferable to losing information.

---

### 39. Manual activity

Activity page:

```text
+ Add activity
```

Allow:

```text
date/time
type
asset
amount
account
counterparty
fees
notes
fiat value
evidence reference
```

Mark it clearly as manual.

---

### 40. Editing imported activity

Never overwrite original data.

Model:

```text
RAW
 ↓
NORMALIZED
 ↓
USER OVERRIDE
 ↓
EFFECTIVE EVENT
```

The UI should expose:

```text
Modified manually

[ View original ]
[ Restore automatic value ]
```

Store:

```text
field
old value
new value
timestamp
reason
```

---

## Part IV — Pricing & Valuations

### 41. CoinGecko integration

Use CoinGecko as the initial primary market-data provider.

Responsibilities:

```text
asset identification
contract mapping
current prices
historical prices
EUR valuation
SEK valuation
```

But CoinGecko must never be treated as permanent storage.

Once a price is used, persist it locally.

---

### 42. Price-provider abstraction

Create:

```python
class PriceProvider:
    current_price(...)
    historical_price(...)
    asset_metadata(...)
```

Initial:

```text
CoinGecko
```

Future:

```text
exchange execution prices
alternative market providers
manual reference prices
```

This prevents vendor lock-in.

---

### 43. Historical valuations

Every economically meaningful event receives:

```text
EUR unit price
EUR value

SEK unit price
SEK value
```

at the relevant timestamp.

Valuation record:

```text
event_id
asset_id
quote_currency
unit_price
total_value

requested_timestamp
observation_timestamp

provider
provider_asset_id

method
granularity
fetched_at
confidence
manual_override
```

---

### 44. Never pretend price precision

Record how the historical price was obtained.

Possible methods:

```text
EXACT_EXECUTION
NEAREST_5_MIN
NEAREST_HOUR
DAILY_REFERENCE
DERIVED_FX
MANUAL
```

This distinction matters for defensible accounting.

---

### 45. Pricing priority

Conceptually:

```text
1. Exact execution price
2. Near-timestamp market observation
3. Hourly historical price
4. Daily historical reference
5. Manual/reference valuation
```

The eventual jurisdiction adapter determines which is appropriate.

---

### 46. Price cache

Create:

```text
price_observations
```

Do not repeatedly ask CoinGecko for the same historical observation.

This makes the application faster, cheaper and more resilient.

---

### 47. Price provenance

Every tax valuation must answer:

> Where did this value come from?

Preserve:

```text
provider
provider asset ID
requested timestamp
observation timestamp
price
currency
method
fetch timestamp
```

---

### 48. Pricing failures

CoinGecko being unavailable must never prevent transaction ingestion.

Instead:

```text
Transaction
✓ stored

Price
⚠ pending
```

Create an Issue and retry later.

---

### 49. Current portfolio pricing

Overview uses current market prices.

Keep current and historical pricing separate:

```text
Historical valuation
→ permanent event evidence

Current valuation
→ live portfolio estimate
```

---

## Part V — Reconciliation

### 50. Internal transfer reconciliation

Example:

```text
Bitget
-0.003001 BTC

Bitcoin wallet
+0.003000 BTC
```

The system should infer:

```text
Internal transfer
Bitget → Bitcoin wallet

Amount: 0.003 BTC
Network fee: 0.000001 BTC
```

rather than treating them as independent economic disposals/acquisitions.

---

### 51. Transfer matcher

Use:

```text
tx hash
asset
network
amount
fee-adjusted amount
addresses
known ownership
timestamps
withdrawal/deposit IDs
```

Calculate confidence.

High confidence:

```text
auto-match
```

Medium:

```text
suggest
```

Low:

```text
leave unresolved
```

Never silently guess ambiguous financial relationships.

---

### 52. Issues/reconciliation

Create Issues for:

```text
missing price
unknown asset
unknown event
unmatched withdrawal
unmatched deposit
possible duplicate
balance mismatch
ambiguous transfer
failed sync
unsupported source record
missing evidence
price conflict
```

Example UI:

```text
Possible internal transfer

Bitget
-0.004201 BTC

Bitcoin wallet
+0.004200 BTC

Difference
0.000001 BTC
Likely network fee

[ Confirm ]
[ Keep separate ]
```

---

### 53. Balance reconciliation

Periodically compare:

```text
ledger-derived balance
        vs
source-reported balance
```

Differences create Issues.

This provides an important independent correctness check.

---

### 54. Data completeness

Calculate:

```text
Raw evidence
Asset identification
Prices
Fees
Transfer matching
Source synchronization
```

Example:

```text
2026

Events                    1,421
Raw evidence               100%
EUR valuations             100%
SEK valuations             100%
Transfers reconciled       99.8%

2 issues
```

---

## Part VI — Tax & Reporting

### 55. Tax-neutral ledger

Never store:

```text
taxable = true
```

as an immutable fact of an event.

Instead:

```text
Canonical event
      ↓
SE / 2026 adapter
      ↓
tax interpretation
```

The same event could be interpreted differently under another jurisdiction or tax year.

---

### 56. Tax adapter

Conceptual interface:

```python
class TaxAdapter:
    jurisdiction
    tax_year

    validate_ledger()
    calculate()
    generate_summary()
    generate_csv()
    generate_pdf()
```

Implementation may use:

```text
RP2
native code
another dependency
```

The frontend should not care.

---

### 57. Jurisdiction selection

Reports page:

```text
Tax year
2026

Country
Sweden
```

The ledger remains unchanged.

Switching to:

```text
Spain
```

reinterprets the same historical facts.

---

### 58. Country/year versioning

Store:

```text
country
tax_year
tax_adapter_version
```

For example:

```text
SE
2026
1.2.0
```

A future 2028 rule change must not make a historical 2026 report unreproducible.

---

### 59. RP2

RP2 is optional.

```text
Canonical Ledger
       ↓
RP2 adapter
       ↓
RP2
       ↓
structured accounting results
       ↓
our reporting UI
```

RP2's ODS files should never become the application's canonical output.

---

### 60. Sweden

Eventually create focused upstream work around Swedish support.

Potential scope:

```text
Swedish accounting support
average-cost methodology where required
SEK
Swedish country plugin
K4 report
official-example tests
```

This can become the public contribution.

The private tracker does not need to become public.

---

### 61. Spain

Support Spain through an appropriate country adapter, potentially leveraging RP2 where suitable.

Always verify the implementation against the tax rules applicable to the selected year.

---

### 62. Other countries

Architecture:

```text
tax/
├── se/
├── es/
└── ...
```

or equivalent plugin mechanism.

Adding Germany, France, Denmark, etc. should not require changing the ledger.

---

### 63. Reports

Reports page has two categories:

```text
Universal
Tax
```

Universal:

```text
Full Ledger CSV
Evidence Archive
Accountant PDF
```

Tax:

```text
Country
Tax year
Readiness
Tax PDF
Tax CSV
RP2 export
```

---

### 64. Full Ledger CSV

Jurisdiction-neutral.

Include as much information as practical:

```text
event_id
timestamp_utc
event_type

asset
amount
secondary_asset
secondary_amount

source_account
destination_account

network
tx_hash

exchange
external_id

fees

EUR unit price
EUR value

SEK unit price
SEK value

price provider
price timestamp
price method

internal transfer
linked events

manual/automatic
override status

description
notes
```

---

### 65. Tax CSV

Separate from the full ledger.

Its format depends on:

```text
country
tax year
```

Do not compromise the canonical export to match one tax authority.

---

### 66. PDF

Generate a clean human-readable PDF containing:

```text
Report identity
Jurisdiction
Tax year
Generation date

Summary
Assets
Acquisitions
Disposals
Gains/losses
Income
Staking/rewards
Fees
Transfers
Manual corrections
Pricing methodology
Source information
Reconciliation status
Detailed event schedule
Methodology
```

The objective is readability for:

```text
you
accountant
tax adviser
tax authority
```

---

### 67. Evidence archive

Generate:

```text
crypto-evidence-2026.zip
```

Containing approximately:

```text
README

ledger/
  events.csv

raw/
  bitget/
  binance/
  bitcoin/
  ...

prices/
  observations.csv

accounts/
  accounts.csv

overrides/
  changes.csv

integrity/
  manifest.json
  hashes.csv

reports/
  report.pdf
  report.csv
```

This provides the underlying evidence behind a report.

---

### 68. Evidence principle

Do not necessarily submit all this information to tax authorities.

Instead:

```text
retain complete evidence privately
              ↓
submit required tax information
              ↓
provide supporting evidence if requested
```

---

### 69. Attachments

Prepare for supporting documents:

```text
receipts
invoices
exchange statements
CSV exports
staking statements
payment confirmations
```

Store:

```text
file metadata
hash
event relationships
```

Evidence files should live on encrypted storage.

---

## Part VII — Operations & Security

### 70. Automatic synchronization

Each connector stores:

```text
last sync
cursor
last success
last failure
status
error
```

Support:

```text
initial backfill
scheduled sync
manual sync
incremental sync
```

Default around 15 minutes where appropriate.

Make configurable.

---

### 71. Idempotency

Synchronization must be safe to repeat.

Use combinations of:

```text
source
external ID
tx hash
log index
content fingerprint
```

to prevent duplicates.

---

### 72. Automatic backups

Backups are mandatory.

Default:

```text
daily automatic backup
```

Suggested retention:

```text
7 daily
4 weekly
12 monthly
```

Configurable.

---

### 73. Backup contents

Back up:

```text
SQLite
raw evidence
price history
attachments
configuration
reports
integrity information
```

Handle secrets securely.

---

### 74. Consistent SQLite backups

Never blindly copy a live DB.

Use SQLite's supported backup/snapshot mechanism.

Then:

```text
create backup
    ↓
verify DB
    ↓
hash
    ↓
encrypt
    ↓
store
```

---

### 75. Backup encryption

Use proven authenticated encryption.

Do not invent cryptography.

The encryption key must not be contained inside the encrypted archive itself.

---

### 76. Off-machine backups

Eventually support:

```text
NAS
second homelab server
external disk
encrypted remote storage
```

Encrypt before leaving the host.

No mandatory cloud dependency.

---

### 77. Backup status UI

Overview/Settings:

```text
Last backup
Today 03:00

✓ Verified
✓ Encrypted

Next
Tomorrow 03:00
```

Failure creates a high-priority Issue.

---

### 78. Restore testing

Provide:

```text
Verify backup
```

Eventually:

```text
Test restore
```

Restore into temporary storage and verify ledger integrity.

---

### 79. Security philosophy

Default:

```text
local-first
self-hosted
no telemetry
no analytics
no advertising
no external account
no mandatory cloud
```

---

### 80. Exchange credentials

Only read-only API credentials.

Never request:

```text
withdraw
trade
transfer
```

permissions unless some future integration genuinely requires something beyond read-only and it is deliberately reviewed.

---

### 81. Wallet credentials

Never store:

```text
seed phrase
private key
xprv
spend key
```

Prefer:

```text
public address
xpub
descriptor
watch-only wallet
view-only access
```

---

### 82. Secret storage

API credentials must be encrypted.

The master key must remain outside SQLite.

For Docker, potentially:

```text
/run/secrets/...
```

with restrictive filesystem permissions.

---

### 83. Host storage

At minimum use:

```text
encrypted host filesystem
encrypted backups
encrypted application secrets
restricted filesystem permissions
```

Full database encryption can be evaluated later if necessary.

---

### 84. Network

Do not expose the application publicly by default.

Prefer:

```text
localhost
LAN
private VPN
```

The homelab VPN is the preferred remote-access mechanism.

---

### 85. Authentication

Support a single local user.

Potentially:

```text
strong password
secure session cookie
```

Do not introduce OAuth/cloud identity unnecessarily.

---

### 86. Audit logs

Record:

```text
sync
manual changes
imports
reports
backups
restores
pricing
connector errors
```

Never log secrets.

---

### 87. Data deletion

Disconnecting a source should mean:

```text
stop synchronization
archive source
retain history
```

not:

```text
delete financial history
```

Permanent deletion should be an explicit advanced operation.

---

## Part VIII — Product

### 88. Overview page

Keep it exceptionally clean.

Primary content:

```text
Portfolio value
EUR / SEK

Assets

24h movement

Realized P/L
Unrealized P/L

Recent activity

Source health

Issues

Backup status
```

---

### 89. Linked Accounts page

Groups:

```text
Exchanges
Wallets
Networks
Lightning
Other
```

Example:

```text
Bitget
✓ Connected
Last sync: 2 min ago

Bitcoin
✓ Synced

MetaMask · Ethereum
✓ Synced

Cake · Monero
○ Not configured

+ Add source
```

Actions:

```text
Sync
Backfill
Edit
Disable
Archive
```

---

### 90. Activity page

Unified chronological ledger.

Desktop:

```text
Date | Activity | Asset | Amount | EUR | SEK | Source | Status
```

Filters:

```text
date
asset
account
source
type
network
manual/automatic
internal/external
resolved/unresolved
```

Search:

```text
transaction hash
exchange ID
address
counterparty
description
```

---

### 91. Event details

Opening an event should expose:

```text
What happened
Date/time

Asset
Amount
EUR
SEK

Source
Destination
Counterparty

Fees

Network
Transaction hash

Price source
Price timestamp
Price methodology

Linked events
Transfer relationship

Evidence
Manual modifications
```

Advanced:

```text
Raw source record
Normalizer version
Integrity hash
```

---

### 92. Reports page

Example:

```text
Reports

Tax year
2026

Country
Sweden

DATA READINESS

✓ Sources synchronized
✓ Raw evidence complete
✓ SEK prices complete
✓ Transfers reconciled
⚠ 1 manual transaction

[ Generate tax report ]

Exports

[ PDF ]
[ Tax CSV ]
[ Full Ledger CSV ]
[ Evidence Archive ]
```

---

### 93. Settings

Keep secondary configuration here:

```text
General
Currencies
Price Providers
Synchronization
Backups
Security
Data
Tax integrations
Advanced
```

---

### 94. Data-quality states

Each event can have:

```text
COMPLETE
PARTIAL
REQUIRES_REVIEW
```

Dimensions:

```text
raw evidence
asset
timestamp
source
price
fee
transfer relationship
```

---

### 95. Report readiness

Before generating tax reports:

```text
Validate ledger
```

Example:

```text
Sweden · 2026

1,421 events

✓ Evidence complete
✓ SEK valuations complete
✓ Fees complete
✓ Transfers reconciled

⚠ 2 manual events
✗ 1 unresolved withdrawal

NOT READY
```

Advanced users may generate with warnings, but deficiencies must never be hidden.

---

### 96. Report reproducibility

Store:

```text
report_id
country
tax_year
generated_at
ledger snapshot/hash
adapter version
price dataset
output hashes
```

Historical reports should remain reproducible.

---

## Part IX — Engineering Practices

### 97. Evidence retention

Default:

```text
retain indefinitely
```

Acquisition information may remain relevant many years later.

Do not purge history automatically.

---

### 98. Schema migrations

Use Alembic from day one.

The database is intended to survive years of application evolution.

Never depend on manually replacing SQLite databases after schema changes.

---

### 99. Tests

Synthetic tests must cover:

```text
buy
sell
swap
deposit
withdrawal
transfer
payment
network fee
staking
interest
mining
gift
margin
futures
Lightning
manual event
missing price
duplicate
unknown event
```

Never commit real personal financial data to Git.

---

### 100. Golden ledger scenarios

Create deterministic synthetic scenarios.

Example:

```text
Buy BTC on Bitget
        ↓
Withdraw BTC
        ↓
Pay network fee
        ↓
Receive in owned wallet
        ↓
Spend some BTC
```

The expected canonical ledger must be known exactly.

---

### 101. CoinGecko tests

Test:

```text
asset resolution
contract resolution
current EUR
current SEK
historical EUR
historical SEK
cache
missing data
fallback
manual override
```

Unit tests should mock/record responses rather than depend on live CoinGecko.

---

### 102. Connector tests

Every connector needs:

```text
pagination
rate limits
duplicate handling
missing fields
malformed response
retry
incremental synchronization
historical backfill
```

---

### 103. Backup tests

Verify:

```text
backup creation
encryption
integrity
restore
ledger equivalence
```

---

### 104. Security tests

At minimum:

```text
credentials encrypted
secrets not exposed through API
secrets not logged
unsafe paths rejected
read-only API assumptions documented
```

---

### 105. Privacy

No:

```text
telemetry
analytics
advertising
tracking
mandatory remote accounts
automatic financial-data uploads
```

External communication should be limited to configured functionality such as:

```text
Bitget
Binance
CoinGecko
blockchain nodes/providers
```

and should be documented.

---

### 106. Data portability

Never lock the user into the application.

Always provide:

```text
Full Ledger CSV
Raw Evidence
Evidence Archive
Database backup
```

Even if the application stops being maintained, the financial history remains recoverable.

---

### 107. Future-proof primitives

Do not attempt to predict every future cryptocurrency system.

Future-proof these concepts instead:

```text
Event
Asset
Account
Source
Network
Fee
Valuation
Evidence
Relationship
Override
```

New technology should be expressible through combinations of these primitives.

---

## Part X — Rollout & Success

### 108. One-day MVP

The objective is **not** to implement the entire master plan in a single day.

The objective is to create the architecture correctly and get one real financial path working.

Must ship:

```text
✓ New repository
✓ React/TypeScript/Vite
✓ Tailwind
✓ Appica UI
✓ FastAPI
✓ SQLite
✓ Alembic

✓ Canonical schema
✓ Accounts
✓ Assets
✓ Raw events
✓ Ledger events
✓ Fees
✓ Valuations
✓ Overrides
✓ Issues
✓ Sync state

✓ Connector abstraction

✓ Bitget connector
✓ Historical Bitget import
✓ Raw Bitget evidence
✓ Bitget normalization

✓ CoinGecko
✓ Asset mapping
✓ Historical EUR prices
✓ Historical SEK prices
✓ Price provenance/cache

✓ Overview
✓ Linked Accounts
✓ Activity
✓ Basic Reports

✓ Manual event creation
✓ Manual corrections

✓ Full Ledger CSV

✓ Automatic backups
✓ Encrypted backups
✓ Backup verification

✓ Destination wallet registration
```

The critical milestone is:

```text
Existing Bitget history
       ↓
import
       ↓
raw evidence
       ↓
canonical ledger
       ↓
EUR + SEK valuation
       ↓
visible in Activity
       ↓
CSV backup/export
```

Only then move the crypto.

---

### 109. After today's MVP

Next:

```text
Bitcoin monitoring
        ↓
Bitget withdrawal ↔ Bitcoin receipt matching
        ↓
Binance
        ↓
EVM / MetaMask
        ↓
Cake BTC
        ↓
Exodus-supported chains
        ↓
Monero
        ↓
Lightning
        ↓
staking / DeFi
        ↓
PDF reports
        ↓
RP2 adapter
        ↓
Spain
        ↓
Sweden
```

---

### 110. Swedish open-source contribution

The private app remains private.

A focused future contribution can instead be:

```text
RP2 Swedish support
```

Potentially:

```text
average-cost accounting support
Swedish country plugin
SEK handling
K4 report generator
official-example tests
```

That is a much cleaner upstream contribution than publishing the entire personal platform.

---

### 111. Definition of success

Consider this real-world lifecycle:

```text
Buy BTC on Bitget
        ↓
app automatically records purchase
        ↓
exact source evidence preserved
        ↓
EUR + SEK valuation stored
        ↓
withdraw to Cake/Bitcoin wallet
        ↓
withdrawal detected
        ↓
blockchain receipt detected
        ↓
internal transfer automatically matched
        ↓
network fee recorded
        ↓
pay for a service with BTC
        ↓
payment detected
        ↓
historical EUR + SEK value attached
        ↓
continue doing this for years
```

Then:

```text
Reports
   ↓
Sweden
   ↓
2026
   ↓
Generate
```

The system already possesses:

```text
every event
every acquisition
every disposal candidate
every transfer
every fee
every historical valuation
every source record
every manual correction
every pricing source
every transaction identifier
every relevant relationship
```

and the Swedish adapter interprets those facts under the appropriate rules.

Selecting:

```text
Spain · 2026
```

uses exactly the same ledger but applies Spanish rules.

That separation is the foundation of the project.

---

### 112. Non-negotiables

**Never:**

- make RP2 the application's database;
- make CoinGecko permanent storage;
- delete raw records after normalization;
- overwrite imported evidence;
- silently guess uncertain transfers;
- invent missing prices;
- identify tokens solely by ticker;
- store seed phrases;
- store private keys;
- store xprvs;
- give exchange APIs withdrawal permissions;
- give exchange APIs unnecessary trading permissions;
- hardcode Swedish tax rules into ledger events;
- hardcode Spanish tax rules into ledger events;
- make ODS the primary output;
- expose the service publicly by default;
- make unencrypted backups;
- allow an API failure to lose a financial event;
- put accounting logic inside React/Appica components;
- couple the application permanently to Appica;
- commit personal financial fixtures to Git.

---

### 113. Final architectural test

Whenever we're uncertain about a technical decision, ask:

> **If Bitget, Binance, Cake, MetaMask, CoinGecko, Appica, RP2 and the current tax implementation all disappeared ten years from now, would the information stored today still let us reconstruct exactly what economically happened?**

If yes, the architecture is doing its job.

If no, **we are depending too heavily on an external system or discarding information we should preserve.**

The end product is therefore not really a "crypto tax app." It is a **private, self-hosted, audit-grade historical ledger for your entire crypto life**, with portfolio tracking and tax reporting built on top.

---

## Part XI — Frontend Quality Bar

### 114. Completely responsive

The application must be **fully responsive across every page** — desktop, tablet, and mobile.

This is not a desktop app with a shrinking layout. Every view is intentionally designed for its breakpoint:

- Overview, Linked Accounts, Activity, Reports, and Settings all render equally well on a phone and a desktop.
- Touch targets, spacing, and tap targets are sized for mobile use.
- Tables become cards; toolbars collapse into action sheets; drawers open as full-screen sheets on small screens.
- Navigation remains accessible and one-thumb friendly on mobile.
- The responsive design is maintained continuously, not bolted on after the fact.

### 115. The UI must be genuinely well-built

"Polished" is a requirement, not a stretch goal.

- Consistent spacing, typography, radius, and border system enforced by design tokens.
- First-class light and dark mode with proper color-token usage — no hardcoded colors.
- Meaningful loading, empty, and skeleton states everywhere data loads.
- Correct focus states, keyboard navigation, and accessible labels.
- Semantic status colors and icons for positive/negative/warning/error/information.
- Restrained, purposeful animation that communicates state changes rather than decorating.
- Cross-browser and cross-device consistency through the responsive design system.
- Domain components wrap Appica so the whole app shares one coherent visual language — never a patchwork of defaults.
- There is **no acceptable state where the app "works but looks unfinished"**; the UI is part of the product.