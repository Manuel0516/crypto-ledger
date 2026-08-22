# Changelog

All notable Crypto Ledger changes are recorded here. This project is currently in beta; releases may include schema, connector, tax, and UI changes that require review before production use.

## [Unreleased]

### Removed

- Solana, Lightning (LND/NWC), and Monero connectors and their tests and documentation; existing accounts of these types remain in the database but no longer sync.
- The evidence-archive subsystem: `/api/reports/evidence.zip`, evidence verification/import endpoints, and the Reports-page verifier. Raw per-event evidence storage is unchanged.

### Changed

- Demo history's Phantom wallet is now a manually tracked account instead of a live Solana source.
- README reflects the reduced source and export surface.

## [v0.2.0-beta.1] — 2026-08-21

### Added

- Binance and Bitget live connector coverage for additional account, derivatives, funding, earn, and transfer records.
- Avalanche C-Chain and BNB Smart Chain support in the EVM wallet source flow.
- Account balance snapshots, precise price provenance, activity reconciliation, issue resolution, and expanded overview data.
- Lightning Network wallet-connect support with read-only permission checks.
- Encrypted backup upload, verification, download, restore, and evidence-archive review flows.
- Configurable currencies, price-provider credentials, synchronization, backups, security, data, and tax-integration settings.
- Desktop and phone product-tour media for public documentation.

### Changed

- Activity and event review now preserve raw evidence while exposing structured correction and resolution workflows.
- The public web container builds the React client once and serves the static bundle through Nginx.
- Web dependency installation uses the committed lockfile through `npm ci`.
- README and third-party notices document the RP2 integration boundary, tax disclaimer, deployment secrets, and exchange-history limitations.
- The Bitget connector reports the available history window and supports normalized JSON imports for older exported records.
- `scripts/verify_release.py` provides an isolated recovery/import smoke test for release preparation.
- `scripts/verify_migrations.py` upgrades a representative persisted pre-release fixture through the current schema head.

### Compatibility and known limitations

- Bitget private APIs generally expose only the most recent 90 days or three months of product history. Older records require exchange exports and review/import outside the live sync window.
- RP2 and country plugins remain separately distributed integrations; they are not bundled or relicensed by Crypto Ledger.
- The Compose file is suitable for a self-hosted deployment baseline, but authentication, TLS, network policy, secret management, and operational backups remain deployment responsibilities.
- Tax reports are informational software output. Users remain responsible for validating their records and filings with a qualified tax professional.
