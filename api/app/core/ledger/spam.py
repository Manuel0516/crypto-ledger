from __future__ import annotations

import json
from collections.abc import Iterable


def _hex_input(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value.startswith("0x") or len(value) < 10 or len(value[2:]) % 64 != 8:
        return None
    try:
        int(value[2:], 16)
    except ValueError:
        return None
    return value


def looks_like_mass_distribution_input(value) -> bool:
    """Detect high-confidence mass-recipient calldata without decoding a ABI.

    Spam/airdrop contracts commonly pass a large dynamic array of recipients.
    We require a long ABI-shaped payload, many address-shaped words, and a
    plausible dynamic-array count. This is a review signal only; it never
    blocks an asset automatically.
    """
    data = _hex_input(value)
    if data is None:
        return False
    words = [data[10 + offset : 10 + offset + 64] for offset in range(0, len(data) - 10, 64)]
    if len(words) < 80:
        return False
    address_words = {
        word[-40:]
        for word in words
        if word[:24] == "0" * 24 and int(word[-40:], 16) != 0
    }
    array_lengths = [int(word, 16) for word in words[:12] if 50 <= int(word, 16) <= 5000]
    return len(address_words) >= 50 and bool(array_lengths)


def suspicious_transaction_hashes(events: Iterable) -> set[str]:
    """Return transaction hashes with a mass-recipient calldata signal."""
    hashes: set[str] = set()
    for event in events:
        raw_event = getattr(event, "raw_event", None)
        if raw_event is None:
            continue
        try:
            payload = json.loads(raw_event.payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not looks_like_mass_distribution_input(payload.get("input")):
            continue
        tx_hash = str(payload.get("hash") or getattr(event, "tx_hash", "") or "").strip().lower()
        if tx_hash:
            hashes.add(tx_hash)
    return hashes
