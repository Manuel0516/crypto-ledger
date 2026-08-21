# Lightning via Nostr Wallet Connect (NWC)

## What this is

A generic [NIP-47 (Nostr Wallet Connect)](https://github.com/nostr-protocol/nips/blob/master/47.md)
Lightning connector. **ZEUS is not a hard dependency** — it's the first
wallet this app is configured against, connected through the exact same
generic protocol integration any other NWC-compatible wallet (Alby, Mutiny,
Cashu-backed wallets, a self-hosted LNbits instance, ...) would use:

```
NWC-compatible Lightning wallet
        |
        v
   NWCConnector   (api/app/connectors/lightning/nwc.py)
        |
        v
   Lightning normalizer   (same file — normalize())
        |
        v
   Canonical ledger   (unchanged: RawEvent -> Event -> Valuation)
```

Adding a wallet to the picker is a UI metadata change (`wallet_software`, a
free-text field, plus an optional named quick-pick in `NWC_WALLETS` in
`AccountsPage.tsx` for wallet-specific "where do I get this string"
instructions), never a new connector — the backend code is identical for
every NWC wallet regardless of whether it has a named quick-pick or not.
**ZEUS and Alby Hub are both wired up today** as named options (Add Account
→ Lightning → NWC wallet); any other NIP-47 wallet (Mutiny, LNbits, Cashu.me,
...) works immediately too via "Other NWC wallet" — same connector, same
code path, just without a dedicated instructions blurb yet.

This app already had one Lightning connector before this feature —
`api/app/connectors/lightning/connector.py` (`LightningConnector`,
`connector_type="lightning_node"`), which talks to your own LND node
directly over its REST API with a macaroon. That connector is unchanged and
still supported; NWC (`connector_type="lightning_nwc"`) is a second,
independent way to connect a Lightning wallet, for people who don't run
their own node.

## Why NWC instead of a ZEUS-specific integration

ZEUS doesn't expose its own remote API — it's a mobile wallet. The way it
(and most modern Lightning wallets) exposes remote read access is NWC, a
standardized protocol built on Nostr. Building against NWC directly, rather
than "ZEUS's API", means every other NWC wallet works for free, and ZEUS
itself could change its internals without breaking this integration as long
as it keeps speaking NIP-47.

## Required permissions — and why the app doesn't need more

NIP-47 connections are scoped: a connection string only grants the specific
methods the wallet chose to expose (visible in its `get_info` response).
This app requests / calls exactly three:

| Method | Used for |
| --- | --- |
| `get_info` | Discovering which methods the connection actually grants (used only to warn you if it grants more than these three — never to unlock anything) |
| `get_balance` | The Lightning balance shown in Overview/reconciliation |
| `list_transactions` | Historical + incremental payment sync |

**`pay_invoice`, `pay_keysend`, `make_invoice`, `make_hold_invoice`,
`cancel_hold_invoice`, and `settle_hold_invoice` are never called anywhere
in this codebase.** This isn't a permission check that could be bypassed —
the code to call them simply doesn't exist in `nwc.py`. If your connection
happens to grant one of those (some wallets don't let you scope a
connection down), `sync_account()` detects it via `get_info` and raises a
warning `Issue` ("`{name} connection grants more than read access`") every
sync until you reconnect with a narrower connection — but the app still
never uses the extra permission.

## Security model

- The NWC connection string (`nostr+walletconnect://...`) is stored the
  same way every other connector's credentials are: Fernet-encrypted inside
  `Account.config_encrypted`, using the app's existing secret-encryption
  mechanism (`app/security/secrets.py`) — no new storage mechanism.
- It is never returned by any API response after storage (`GET /api/accounts`
  only ever returns `has_config: bool`) and never appears in an export.
- Nothing in this feature's code path calls `str()`/`repr()`/logs the raw
  `NostrWalletConnectUri` object or its `secret()` — doing so would
  reconstruct the full connection string. Only `.public_key().to_hex()` (a
  public key, not a secret) is ever surfaced, used solely to namespace raw
  evidence per connection.
- No private key, seed phrase, or macaroon-equivalent spending credential is
  ever requested or stored. The `secret` inside an NWC connection string is
  a single-purpose signing key for *this one connection* (per NIP-47, wallet
  services recommend a unique key per app) — the app never sees your
  wallet's actual keys.
- The Schnorr signing and NIP-04/NIP-44 encryption NWC needs are delegated
  to [`nostr-sdk`](https://pypi.org/project/nostr-sdk/) (rust-nostr's
  official Python bindings), not hand-rolled — a subtly wrong signature
  scheme or encryption padding is not a mistake worth risking by hand for a
  live wallet connection.

## How to connect a wallet

The last two steps are identical regardless of which wallet you use — only
getting the connection string differs:

**ZEUS**

1. **Settings → Wallets → your node → Nostr Wallet Connect** (or
   **Settings → Nostr Wallet Connect**, depending on ZEUS version) → **Add
   connection**.
2. Give it a name (e.g. "crypto-ledger") and grant it read-only access if
   ZEUS offers granular scoping — `get_balance`, `get_info`, and
   `list_transactions` (or equivalent) are all this app needs.
3. Copy the `nostr+walletconnect://...` connection string ZEUS gives you.

**Alby Hub** (self-hosted — [getalby.com/hub](https://getalby.com/hub))

1. **Connections → Add Connection**.
2. Name it (e.g. "crypto-ledger"). Alby Hub defaults a new connection to
   "Full Access" — **uncheck every send/pay permission** and leave only the
   read ones (balance, transaction history) checked, then **Next**.
3. Copy the pairing secret (`nostr+walletconnect://...`) it shows you.

**Any other NIP-47 wallet** — look for "Nostr Wallet Connect" in its
settings, scope the connection to read-only if it offers granular
permissions, and copy the connection string it gives you. Nothing about
this app's side changes.

**Then, in this app:**

4. **Linked Accounts → Add account → Lightning → NWC wallet**, pick ZEUS /
   Alby Hub / Other (only affects the default name and the instructions
   shown — every option registers the exact same `connector_type`), paste
   the connection string, **Register source**.
5. The app validates the string, pulls your current balance, and imports
   available history immediately. Re-run **Backfill** any time; **Sync**
   picks up new activity going forward, and runs automatically on the
   interval set in Settings → Synchronization.

## How synchronization works

- **Backfill** (`since=None`): requests all history NWC's `list_transactions`
  will return, no lower time bound.
- **Sync** (incremental): requests only transactions since the account's
  `last_sync`.
- Only **terminal** transactions (`SETTLED` or `FAILED`/`EXPIRED`) are
  imported. A `PENDING`/`ACCEPTED` (in-flight hold invoice) transaction is
  not yielded yet — it will appear on a later sync once it resolves. This
  mirrors how the Bitcoin connector waits for on-chain confirmation before
  importing a transaction, for the same reason: raw evidence rows are never
  mutated once stored, so importing a still-pending transaction now would
  mean it could never be corrected to its final state later without
  becoming a duplicate.
- A `SETTLED` payment becomes `LIGHTNING_SEND`/`LIGHTNING_RECEIVE`
  (`status=COMPLETE`); its fee (if any) is attached as a `LIGHTNING_FEE` on
  the same event, not a separate one — matching how every other connector
  in this app attaches trading/network fees. A `FAILED`/`EXPIRED`
  transaction is still preserved as raw evidence (nothing is silently
  discarded) but becomes a zero-amount event with `status=REQUIRES_REVIEW`,
  since no funds actually moved.
- Deduplication is by payment hash (falling back to a timestamp+amount
  fingerprint only if a wallet service omits it) via this app's existing
  `(source_id, external_id)` uniqueness on raw evidence — re-running sync
  never duplicates a payment.
- Pricing (EUR/SEK historical valuation) is not reimplemented for
  Lightning — every Lightning event goes through the same `ingest()` →
  `refresh_valuations()` path as every other connector.

## Portfolio & on-chain unification

BTC moved over Lightning is still BTC — it isn't modeled as a separate
asset from on-chain BTC (unlike, say, a bridged token on a different
chain). Both resolve to the same canonical `(BTC, Bitcoin, —)` asset, so
Overview reports one combined BTC total across every connected source
(on-chain wallets, exchanges, and Lightning), while `event_subtype` and the
owning account still distinguish a Lightning payment from an on-chain
transaction in Activity. This also fixed the existing LND-based
`lightning_node` connector, which previously tagged Lightning BTC as a
distinct `"Lightning"`-network asset — fragmenting a user's real BTC total
across two line items whenever both an on-chain and a Lightning source were
connected.

Full on-chain/Lightning reconciliation (recognizing a channel-open/close as
linked to its funding transaction) is not implemented — see Known
limitations.

## Known limitations

- **Channel open/close is not modeled by the NWC connector.** NIP-47 has no
  channel-management method — it's out of scope for a Lightning *wallet*
  protocol (channel management is a node-operator concern). The existing
  `lightning_node` (LND) connector does emit
  `LIGHTNING_CHANNEL_OPEN`/`LIGHTNING_CHANNEL_CLOSE`, for users running
  their own node.
- **A pending transaction that never resolves never appears.** Since
  non-terminal transactions aren't imported yet (see above), if a wallet
  service never reports a final `SETTLED`/`FAILED`/`EXPIRED` state for some
  transaction, it simply never surfaces. This should be rare in practice —
  most wallet services resolve within seconds to minutes.
- **No live/push notifications.** NIP-47 defines an optional notification
  extension (wallet service pushes new-payment events instead of the client
  polling). This connector polls `list_transactions` on each sync — reliable
  and simple for a first implementation, per the brief. Point of extension:
  a future notification-based connector could subscribe to NWC's
  notification kind and call the same `normalize()` this connector already
  has, without changing the canonical ledger at all.
- **No automatic price re-fetch for previously-failed valuations.** This is
  a pre-existing limitation of the pricing pipeline, not specific to
  Lightning — a price that failed to resolve at ingest time isn't retried
  automatically on a later sync (editing the event's amount/timestamp does
  retry it). Same behavior as every other connector.
- **This has not been tested against a real wallet or a live relay** — not
  ZEUS, not Alby Hub, not any other NWC service — only against
  `nostr-sdk`'s real object model with synthetic/hand-built responses (see
  `api/tests/test_nwc_connector.py`). The connector code makes no
  ZEUS-specific or Alby-specific assumptions (confirmed: nothing in
  `nwc.py` branches on wallet identity, and encryption scheme negotiation —
  wallets differ on NIP-04 vs NIP-44 support — is handled internally by
  `nostr-sdk`, not by this codebase), but "should be generic per the spec"
  and "verified against a specific wallet's real behavior" are different
  claims. Treat your first real connection, to whichever wallet you use, as
  the actual verification — the same caveat the existing LND connector's
  code comments already carry for the same reason (no reachable node/wallet
  in the environment this was built in).
- **`nostr-sdk` is pre-1.0 ("alpha" per its own PyPI description).** Its
  Python API may change in a breaking way on a future upgrade; the version
  is pinned in `api/requirements.txt` (`nostr-sdk>=0.45,<0.46`) rather than
  left open, specifically to avoid an unreviewed breaking change reaching a
  wallet-connection code path.

## Adding another NWC wallet

Already works today via **Other NWC wallet** in the Add Account form — no
code needed, `wallet_software` accepts any name. Nothing to change in the
connector, sync, normalization, pricing, or reconciliation code; none of it
is wallet-specific.

To give a wallet its own named quick-pick (a default name + its own
"where do I get this string" instructions, like ZEUS and Alby Hub have),
add an entry to the `NWC_WALLETS` array in `web/src/pages/AccountsPage.tsx`
— that's the entire change.
