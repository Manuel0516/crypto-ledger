from __future__ import annotations

import hashlib
import hmac
import struct

import base58
import bech32
from Crypto.Hash import RIPEMD160
from ecdsa import SECP256k1
from ecdsa.ellipticcurve import Point

# BIP32 public-key-only derivation (CKDpub) from an xpub/ypub/zpub. This
# module never sees, stores, or could derive a private key or seed — it
# only ever does public elliptic-curve point addition (plan §32/§81).
# RIPEMD160 comes from pycryptodome rather than hashlib because OpenSSL 3.x
# disables it by default on some systems, which would make hashlib silently
# unavailable in production even though it works here in dev.

_CURVE = SECP256k1.curve
_GENERATOR = SECP256k1.generator
_ORDER = SECP256k1.order

# version bytes -> BIP standard -> address type this key derives
_VERSIONS: dict[bytes, str] = {
    bytes.fromhex("0488b21e"): "p2pkh",  # xpub (BIP44, legacy)
    bytes.fromhex("049d7cb2"): "p2sh-p2wpkh",  # ypub (BIP49, P2SH-wrapped SegWit)
    bytes.fromhex("04b24746"): "p2wpkh",  # zpub (BIP84, native SegWit)
}


class InvalidExtendedKey(ValueError):
    pass


def _hash160(data: bytes) -> bytes:
    return RIPEMD160.new(hashlib.sha256(data).digest()).digest()


def _decompress_pubkey(compressed: bytes) -> Point:
    if len(compressed) != 33 or compressed[0] not in (2, 3):
        raise InvalidExtendedKey("Invalid compressed public key in extended key")
    x = int.from_bytes(compressed[1:], "big")
    p = _CURVE.p()
    y_squared = (pow(x, 3, p) + _CURVE.a() * x + _CURVE.b()) % p
    y = pow(y_squared, (p + 1) // 4, p)
    if y % 2 != compressed[0] % 2:
        y = p - y
    return Point(_CURVE, x, y)


def _compress_pubkey(point: Point) -> bytes:
    prefix = 2 if point.y() % 2 == 0 else 3
    return bytes([prefix]) + int(point.x()).to_bytes(32, "big")


def decode_extended_key(key: str) -> dict:
    try:
        raw = base58.b58decode_check(key)
    except Exception as exc:
        raise InvalidExtendedKey(f"Could not decode extended public key: {exc}") from exc
    if len(raw) != 78:
        raise InvalidExtendedKey("Extended key has the wrong length")
    key_type = _VERSIONS.get(raw[0:4])
    if key_type is None:
        raise InvalidExtendedKey("Unrecognized extended key version — expected an xpub, ypub, or zpub")
    pubkey_bytes = raw[45:78]
    if pubkey_bytes[0] not in (2, 3):
        raise InvalidExtendedKey("This looks like a private extended key (xprv/yprv/zprv) — only public keys are accepted")
    return {"key_type": key_type, "chain_code": raw[13:45], "pubkey": pubkey_bytes}


def _ckd_pub(pubkey: bytes, chain_code: bytes, index: int) -> tuple[bytes, bytes]:
    if index >= 0x80000000:
        raise InvalidExtendedKey("Cannot derive a hardened child from a public key alone")
    digest = hmac.new(chain_code, pubkey + struct.pack(">I", index), hashlib.sha512).digest()
    il, ir = digest[:32], digest[32:]
    il_int = int.from_bytes(il, "big")
    if il_int >= _ORDER:  # astronomically unlikely per BIP32
        raise InvalidExtendedKey("Derivation produced an invalid child key at this index")
    child_point = _GENERATOR * il_int + _decompress_pubkey(pubkey)
    return _compress_pubkey(child_point), ir


def _pubkey_to_address(pubkey: bytes, key_type: str) -> str:
    h160 = _hash160(pubkey)
    if key_type == "p2pkh":
        return base58.b58encode_check(b"\x00" + h160).decode()
    if key_type == "p2sh-p2wpkh":
        redeem_script = b"\x00\x14" + h160
        return base58.b58encode_check(b"\x05" + _hash160(redeem_script)).decode()
    if key_type == "p2wpkh":
        program = bech32.convertbits(h160, 8, 5)
        return bech32.bech32_encode("bc", [0] + program)
    raise InvalidExtendedKey(f"Unsupported key type: {key_type}")


def derive_addresses(xkey: str, count: int, *, change: bool = False, start: int = 0) -> list[str]:
    """Derives `count` receive (change=False) or change (change=True)
    addresses from an xpub/ypub/zpub, starting at index `start`. Standard
    BIP44/49/84 layout: account_xpub -> chain (0=external/1=internal) ->
    address_index, both non-hardened."""
    decoded = decode_extended_key(xkey)
    branch_pubkey, branch_chain_code = _ckd_pub(decoded["pubkey"], decoded["chain_code"], 1 if change else 0)
    addresses = []
    for i in range(start, start + count):
        child_pubkey, _ = _ckd_pub(branch_pubkey, branch_chain_code, i)
        addresses.append(_pubkey_to_address(child_pubkey, decoded["key_type"]))
    return addresses
