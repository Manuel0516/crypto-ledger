from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

RPC_URL = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# A few well-known SPL mints, so common stablecoins show a real symbol
# instead of a truncated mint address. Anything else still gets tracked
# correctly — just labeled by its mint (plan §25: never *pretend* an
# identity we don't actually know).
KNOWN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
}


class SolanaAddressConnector:
    """Read-only Solana address tracking via the public JSON-RPC endpoint.
    Uses pre/post balance diffs — lamports for native SOL, and
    preTokenBalances/postTokenBalances for SPL tokens — rather than parsing
    instructions, so it stays correct regardless of which program moved the
    funds. One transaction can move native SOL and several SPL tokens at
    once; each non-zero leg becomes its own canonical event (plan §33's
    "one chain tx may become several events", applied to Solana)."""

    source_id = "solana"

    def __init__(self, address: str, account_label: str):
        self.address = address
        self.account_label = account_label

    @property
    def version(self) -> str:
        return "solana-address-0.4"

    def _rpc(self, method: str, params: list) -> dict | None:
        return (self._rpc_batch([(method, params)]) or [None])[0]

    def _rpc_batch(self, calls: list[tuple[str, list]]) -> list[dict | None]:
        """One HTTP round trip for several RPC calls (JSON-RPC 2.0 batching).

        A first backfill can need a couple hundred ``getTransaction`` calls;
        issuing those one at a time (each with its own throttle sleep) made a
        single busy wallet's backfill take several minutes. Batching them
        collapses that to one request per signature page, at the same
        request rate the public RPC already saw for the individual calls.
        """
        if not calls:
            return []
        body = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params} for i, (method, params) in enumerate(calls)]
        backoff = 1.5
        for attempt in range(5):
            time.sleep(0.1)  # the public RPC rate-limits aggressively under a burst of calls
            try:
                response = httpx.post(RPC_URL, json=body, timeout=30.0)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429 and attempt < 4:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise ConnectorUnavailable(f"Could not reach Solana RPC: {exc}") from exc
            # A single-call batch, some RPC providers reply with one object
            # instead of a one-element array.
            items = data if isinstance(data, list) else [data]
            by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
            # A batch this size can get a per-item rate-limit error (code
            # 429) even on an HTTP 200 — the whole request needs retrying,
            # not just the one call, since the provider is shedding load.
            if attempt < 4 and any(item.get("error", {}).get("code") == 429 for item in by_id.values()):
                time.sleep(backoff)
                backoff *= 2
                continue
            results: list[dict | None] = []
            for i in range(len(calls)):
                item = by_id.get(i)
                if item is None:
                    results.append(None)
                    continue
                if "error" in item:
                    raise ConnectorUnavailable(f"Solana RPC error: {item['error']}")
                results.append(item.get("result"))
            return results
        raise ConnectorUnavailable("Solana's public RPC is rate-limiting this host — try again shortly")

    BATCH_SIZE = 25
    BACKFILL_BATCHES = 8  # since=None (first sync) -> up to 200 signatures
    # getTransaction responses are large; a batch of 25 of them in one
    # request gets rate-limited by the public RPC even after backing off.
    # Chunking keeps most of the round-trip savings over one-call-per-tx
    # while staying under that ceiling.
    GET_TX_CHUNK_SIZE = 5

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        batches = self.BACKFILL_BATCHES if since is None else 1
        before: str | None = None
        namespace = f"{self.source_id}:{self.address}"

        for _ in range(batches):
            params: dict = {"limit": self.BATCH_SIZE}
            if before:
                params["before"] = before
            signatures = self._rpc("getSignaturesForAddress", [self.address, params]) or []
            if not signatures:
                return

            valid = [s["signature"] for s in signatures if not s.get("err")]
            for chunk_start in range(0, len(valid), self.GET_TX_CHUNK_SIZE):
                chunk = valid[chunk_start : chunk_start + self.GET_TX_CHUNK_SIZE]
                txs = self._rpc_batch(
                    [("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]) for sig in chunk]
                )
                for signature, tx in zip(chunk, txs):
                    if tx is None:
                        continue
                    timestamp = tx.get("blockTime")
                    source_timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None

                    if self._net_lamports(tx):
                        yield RawRecord(namespace, signature, source_timestamp, {"tx": tx, "_leg": "native"})

                    for mint, net, decimals in self._token_legs(tx):
                        payload = {"tx": tx, "_leg": "token", "_mint": mint, "_net": net, "_decimals": decimals}
                        yield RawRecord(namespace, f"{signature}-token-{mint}", source_timestamp, payload)

            if len(signatures) < self.BATCH_SIZE:
                return
            before = signatures[-1]["signature"]

    def _account_index(self, tx: dict) -> int | None:
        addresses = self._account_addresses(tx)
        return addresses.index(self.address) if self.address in addresses else None

    def _account_addresses(self, tx: dict) -> list[str]:
        """Return account addresses in the same order as Solana balance arrays.

        Versioned transactions append writable/readonly lookup-table accounts
        after the static message keys.  Keeping those addresses here means a
        the external wallet can still be identified when it came from an address
        lookup table rather than the static account list.
        """
        keys = tx["transaction"]["message"].get("accountKeys") or []
        addresses = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
        loaded = tx.get("meta", {}).get("loadedAddresses") or {}
        addresses.extend(loaded.get("writable") or [])
        addresses.extend(loaded.get("readonly") or [])
        return addresses

    def _net_lamports(self, tx: dict) -> int | None:
        idx = self._account_index(tx)
        if idx is None:
            return None
        fee = tx["meta"].get("fee", 0) if idx == 0 else 0
        net = (tx["meta"]["postBalances"][idx] - tx["meta"]["preBalances"][idx]) + fee
        return net or None

    def _token_legs(self, tx: dict) -> list[tuple[str, int, int]]:
        """Per-mint net raw-unit change for token accounts owned by our
        tracked address, matched by accountIndex between pre/post so a
        newly-opened or closed token account is handled correctly too."""
        pre = {b["accountIndex"]: b for b in (tx["meta"].get("preTokenBalances") or [])}
        post = {b["accountIndex"]: b for b in (tx["meta"].get("postTokenBalances") or [])}
        legs: list[tuple[str, int, int]] = []
        for idx in set(pre) | set(post):
            entry = post.get(idx) or pre.get(idx)
            if entry.get("owner") != self.address:
                continue
            pre_amount = int(pre[idx]["uiTokenAmount"]["amount"]) if idx in pre else 0
            post_amount = int(post[idx]["uiTokenAmount"]["amount"]) if idx in post else 0
            net = post_amount - pre_amount
            if net == 0:
                continue
            decimals = int(entry["uiTokenAmount"]["decimals"])
            legs.append((entry["mint"], net, decimals))
        return legs

    def _native_external_address(self, tx: dict, net: int, own_index: int | None) -> str | None:
        """Pick the largest opposite balance change as the other side.

        Solana does not expose a single from/to pair for every transaction,
        but ordinary system transfers leave a clear opposite balance delta.
        For program transactions this is intentionally best-effort: the
        canonical event still keeps the transaction hash and our own address
        even when no unique external wallet can be inferred.
        """
        if own_index is None or not net:
            return None
        addresses = self._account_addresses(tx)
        pre = tx.get("meta", {}).get("preBalances") or []
        post = tx.get("meta", {}).get("postBalances") or []
        wanted_sign = -1 if net > 0 else 1
        candidates: list[tuple[int, str]] = []
        for index, address in enumerate(addresses):
            if index == own_index or not address or index >= len(pre) or index >= len(post):
                continue
            delta = post[index] - pre[index]
            if delta * wanted_sign > 0:
                candidates.append((abs(delta), address))
        return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    def _token_external_address(self, tx: dict, mint: str, net: int) -> str | None:
        """Infer the external token-account owner with the opposite mint diff."""
        pre = {b["accountIndex"]: b for b in (tx.get("meta", {}).get("preTokenBalances") or [])}
        post = {b["accountIndex"]: b for b in (tx.get("meta", {}).get("postTokenBalances") or [])}
        addresses = self._account_addresses(tx)
        by_party: dict[str, int] = {}
        for index in set(pre) | set(post):
            before, after = pre.get(index), post.get(index)
            entry = after or before
            if not entry or entry.get("mint") != mint:
                continue
            if before and after and before.get("mint") != after.get("mint"):
                continue
            before_amount = int(before["uiTokenAmount"]["amount"]) if before else 0
            after_amount = int(after["uiTokenAmount"]["amount"]) if after else 0
            delta = after_amount - before_amount
            party = entry.get("owner")
            if not party and index < len(addresses):
                party = addresses[index]
            if not party or party == self.address:
                continue
            by_party[party] = by_party.get(party, 0) + delta

        wanted_sign = -1 if net > 0 else 1
        candidates = [(abs(delta), party) for party, delta in by_party.items() if delta * wanted_sign > 0]
        return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    def fetch_balances(self) -> Iterable[Balance]:
        balances: list[Balance] = []

        native = self._rpc("getBalance", [self.address])
        lamports = (native or {}).get("value") if isinstance(native, dict) else None
        if lamports:
            balances.append(Balance("SOL", f"{lamports / 1e9:.9f}", asset_network="Solana"))

        token_accounts = self._rpc(
            "getTokenAccountsByOwner",
            [self.address, {"programId": TOKEN_PROGRAM_ID}, {"encoding": "jsonParsed"}],
        )
        for entry in (token_accounts or {}).get("value", []):
            info = entry.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            token_amount = info.get("tokenAmount", {})
            ui_amount = token_amount.get("uiAmount")
            if not ui_amount:
                continue
            mint = info.get("mint", "")
            symbol, _ = KNOWN_MINTS.get(mint, (f"{mint[:4]}…{mint[-4:]}" if mint else "UNKNOWN", None))
            decimals = token_amount.get("decimals", 9)
            balances.append(Balance(symbol, f"{ui_amount:.{min(decimals, 18)}f}", asset_network="Solana", asset_contract=mint))
        return balances

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)

        if payload["_leg"] == "token":
            mint, net, decimals = payload["_mint"], payload["_net"], payload["_decimals"]
            symbol, known_decimals = KNOWN_MINTS.get(mint, (f"{mint[:4]}…{mint[-4:]}", decimals))
            event_type, direction = ("DEPOSIT", "+") if net >= 0 else ("WITHDRAWAL", "-")
            signature = raw.external_id.split("-token-")[0]
            external_address = self._token_external_address(payload["tx"], mint, net)
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="spl_token",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=symbol,
                asset_network="Solana",
                asset_contract=mint,
                asset_type="TOKEN",
                amount=f"{abs(net) / (10 ** known_decimals):.{min(known_decimals, 18)}f}",
                account_name=self.account_label,
                address_from=external_address if net >= 0 else self.address,
                address_to=self.address if net >= 0 else external_address,
                notes=f"SPL transfer · {signature[:12]}…",
                tx_hash=signature,
                block_height=payload["tx"].get("slot"),
                contract_address=mint,
            )

        tx = payload["tx"]
        idx = self._account_index(tx)
        net = self._net_lamports(tx) or 0
        fee_lamports = tx["meta"].get("fee", 0) if idx == 0 else 0
        external_address = self._native_external_address(tx, net, idx)

        event_type, direction = ("DEPOSIT", "+") if net >= 0 else ("WITHDRAWAL", "-")
        fees: list[NormalizedFee] = []
        if fee_lamports:
            fees.append(NormalizedFee(fee_type="NETWORK_FEE", asset_symbol="SOL", amount=f"{fee_lamports / 1e9:.9f}"))

        return NormalizedEvent(
            event_type=event_type,
            event_subtype="native",
            direction=direction,
            status="COMPLETE",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol="SOL",
            asset_network="Solana",
            amount=f"{abs(net) / 1e9:.9f}",
            account_name=self.account_label,
            address_from=external_address if net >= 0 else self.address,
            address_to=self.address if net >= 0 else external_address,
            notes=f"On-chain tx {raw.external_id[:12]}…",
            fees=fees,
            tx_hash=raw.external_id,
            block_height=tx.get("slot"),
        )
