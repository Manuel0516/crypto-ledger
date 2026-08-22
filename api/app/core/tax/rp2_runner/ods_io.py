from __future__ import annotations

import configparser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import ezodf
from sqlalchemy.orm import Session

from app.core.pricing.cache import get_historical_prices
from app.core.pricing.config import configured_price_provider
from app.core.pricing.provider import historical_unit_price
from ..common import EffectiveEvent, TransferPair, is_liquidity_reward

# RP2 event vocabulary (docs/input_files.md). Anything not mapped here
# (fee-only entries, UNKNOWN, unclassified WITHDRAWAL/DEPOSIT) never reaches
# this stage — readiness blocks report generation on unclassified transfers,
# and other unmapped types simply aren't cost-basis events.
IN_TYPE_MAP = {
    "BUY": "BUY", "AIRDROP": "AIRDROP", "INCOME": "INCOME", "INTEREST": "INTEREST",
    "STAKING_REWARD": "STAKING", "MINING_REWARD": "MINING", "GIFT_RECEIVED": "GIFT",
}
# RP2 has no dedicated "spent on goods/services" category; PAYMENT is
# economically a disposal at market value, same as SELL.
OUT_TYPE_MAP = {
    "SELL": "SELL", "PAYMENT": "SELL", "DONATION": "DONATE", "GIFT_SENT": "GIFT", "LOST": "LOST",
}

_IN_HEADER = ["timestamp", "exchange", "holder", "asset", "transaction_type", "spot_price", "crypto_in", "fiat_fee", "unique_id", "notes"]
_OUT_HEADER = ["timestamp", "exchange", "holder", "asset", "transaction_type", "spot_price", "crypto_out_no_fee", "crypto_fee", "fiat_fee", "unique_id", "notes"]
_INTRA_HEADER = ["timestamp", "from_exchange", "from_holder", "to_exchange", "to_holder", "asset", "spot_price", "crypto_sent", "crypto_received", "unique_id", "notes"]
# RP2 parses these columns as numbers, not text — everything else (including
# timestamp, which it parses from a string) must be written as text.
_NUMERIC_FIELDS = {"spot_price", "crypto_in", "crypto_out_no_fee", "crypto_fee", "fiat_fee", "crypto_sent", "crypto_received"}


@dataclass
class _AssetRows:
    in_rows: list[list[str]] = field(default_factory=list)
    out_rows: list[list[str]] = field(default_factory=list)
    intra_rows: list[list[str]] = field(default_factory=list)


def _iso(event: EffectiveEvent) -> str:
    # SQLite round-trips DateTime(timezone=True) columns as naive datetimes
    # even though every value is stored/produced as UTC — RP2 requires an
    # explicit offset, so assume UTC when tzinfo is missing rather than
    # let RP2 reject an otherwise-correct timestamp.
    occurred_at = event.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.isoformat()


def _eur_unit_price(event: EffectiveEvent) -> Decimal | None:
    for valuation in event.valuations:
        if valuation.quote_currency == "EUR":
            return Decimal(valuation.unit_price)
    return None


def _fee_eur_value(session: Session, event: EffectiveEvent) -> tuple[Decimal, bool]:
    """Sums this event's fees in EUR. Returns (total, any_unpriced) — a fee
    in a different asset than the primary one is priced independently via
    the same historical-price cache the ledger uses; only genuinely unpriceable
    assets (no coingecko_id, or the provider has nothing for that day) fall
    back to 0 and set any_unpriced=True so the caller can warn about it."""
    total = Decimal(0)
    unpriced = False
    primary_price = _eur_unit_price(event)
    for fee in event.fees:
        amount = Decimal(fee.fee_amount)
        if fee.fee_asset_id == event.primary_asset_id and primary_price is not None:
            total += amount * primary_price
            continue
        if fee.fee_asset.coingecko_id:
            prices = get_historical_prices(session, configured_price_provider(session), fee.fee_asset.coingecko_id, event.occurred_at, ["EUR"])
            if "EUR" in prices:
                total += amount * historical_unit_price(prices["EUR"])
                continue
        unpriced = True
    return total, unpriced


def build_input(session: Session, events: list[EffectiveEvent], pairs: list[TransferPair], taxpayer_name: str, work_dir: Path) -> tuple[Path, Path, list[str]]:
    """Writes the RP2 input ODS + INI config into work_dir. Returns
    (ods_path, ini_path, warnings) — events RP2 can't use (no EUR price)
    are skipped and reported as warnings rather than silently dropped."""
    work_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    skip_ids = {e.id for pair in pairs for e in (pair.withdrawal, pair.deposit)}
    by_asset: dict[str, _AssetRows] = defaultdict(_AssetRows)
    exchanges: set[str] = set()
    assets: set[str] = set()
    any_fee_unpriced = False

    def label(value: str | None) -> str:
        value = value or "Unknown"
        exchanges.add(value)
        return value

    for event in events:
        if event.id in skip_ids:
            continue
        asset = event.primary_asset.symbol
        # Activity amounts may be stored as signed values for manual entries
        # and corrections. RP2's crypto quantity fields are magnitudes; the
        # table type already carries the direction.
        amount = abs(Decimal(event.primary_amount))
        fee_eur, unpriced = _fee_eur_value(session, event)
        any_fee_unpriced = any_fee_unpriced or unpriced

        effective_type = "INCOME" if is_liquidity_reward(event) else event.event_type
        if event.event_type == "LIQUIDITY" and not is_liquidity_reward(event):
            warnings.append(f"Event #{event.id} (LIQUIDITY) remains in the activity schedule; generic liquidity add/remove is not included in RP2 tax totals.")
            continue

        if effective_type in IN_TYPE_MAP:
            unit_price = _eur_unit_price(event)
            if unit_price is None:
                warnings.append(f"Event #{event.id} ({event.event_type} {amount} {asset}) has no EUR price yet — excluded from the RP2 input.")
                continue
            assets.add(asset)
            by_asset[asset].in_rows.append([_iso(event), label(event.wallet_display), taxpayer_name, asset, IN_TYPE_MAP[effective_type], str(unit_price), str(amount), str(fee_eur), str(event.id), ""])

        elif event.event_type == "SWAP":
            if event.secondary_asset is None or event.secondary_amount in (None, "") or Decimal(event.secondary_amount) == 0:
                warnings.append(f"Event #{event.id} (SWAP {amount} {asset}) is missing its incoming asset or amount — excluded from the RP2 input.")
                continue
            unit_price = _eur_unit_price(event)
            if unit_price is None:
                warnings.append(f"Event #{event.id} (SWAP {amount} {asset}) has no EUR price yet — excluded from the RP2 input.")
                continue
            assets.add(asset)
            by_asset[asset].out_rows.append([_iso(event), label(event.wallet_display), taxpayer_name, asset, "SELL", str(unit_price), str(amount), "0", str(fee_eur), str(event.id), ""])
            sec_asset = event.secondary_asset.symbol
            sec_amount = Decimal(event.secondary_amount)
            sec_unit_price = (unit_price * amount) / sec_amount
            assets.add(sec_asset)
            by_asset[sec_asset].in_rows.append([_iso(event), label(event.wallet_display), taxpayer_name, sec_asset, "BUY", str(sec_unit_price), str(sec_amount), "0", f"{event.id}-swap-in", ""])

        elif event.event_type in OUT_TYPE_MAP:
            is_loss = event.event_type == "LOST"
            unit_price = Decimal(0) if is_loss else _eur_unit_price(event)
            if unit_price is None:
                warnings.append(f"Event #{event.id} ({event.event_type} {amount} {asset}) has no EUR price yet — excluded from the RP2 input.")
                continue
            assets.add(asset)
            by_asset[asset].out_rows.append([_iso(event), label(event.wallet_display), taxpayer_name, asset, OUT_TYPE_MAP[event.event_type], str(unit_price), str(amount), "0", str(fee_eur), str(event.id), ""])

    for pair in pairs:
        w, d = pair.withdrawal, pair.deposit
        asset = w.primary_asset.symbol
        assets.add(asset)
        unit_price = _eur_unit_price(w) or _eur_unit_price(d)
        by_asset[asset].intra_rows.append([_iso(w), label(w.wallet_display), taxpayer_name, label(d.wallet_display), taxpayer_name, asset, str(unit_price) if unit_price is not None else "", str(abs(Decimal(w.primary_amount))), str(abs(Decimal(d.primary_amount))), str(w.id), ""])

    # RP2 requires an acquisition (IN) table before it can process any
    # disposal or transfer for an asset. Deposits are deliberately
    # schedule-only in this ledger, so a disposal can legitimately have no
    # acquisition row available to RP2. Keep that activity in the shared
    # schedule, but omit only its affected RP2 rows with a visible warning.
    for asset in list(by_asset):
        rows = by_asset[asset]
        if rows.in_rows:
            continue
        if rows.out_rows or rows.intra_rows:
            warnings.append(
                f"Asset {asset} has no acquisition rows available to RP2; its affected tax rows were excluded while the activities remain in the schedule."
            )
        del by_asset[asset]
        assets.discard(asset)

    if any_fee_unpriced:
        warnings.append("Some fees were in an asset with no known EUR price for that day — those fees contribute 0 to the totals rather than being guessed.")
    ods_path = work_dir / "rp2_input.ods"
    ini_path = work_dir / "rp2_config.ini"
    _write_ods(ods_path, by_asset)
    _write_ini(ini_path, sorted(assets), sorted(exchanges), taxpayer_name)
    return ods_path, ini_path, warnings


def _write_table(sheet: ezodf.Sheet, start_row: int, keyword: str, header: list[str], rows: list[list[str]]) -> int:
    sheet[start_row, 0].set_value(keyword)
    for col, name in enumerate(header):
        sheet[start_row + 1, col].set_value(name)
    for offset, row in enumerate(rows):
        for col, value in enumerate(row):
            if value == "":
                continue
            if header[col] in _NUMERIC_FIELDS:
                sheet[start_row + 2 + offset, col].set_value(float(value))
            else:
                sheet[start_row + 2 + offset, col].set_value(value)
    end_row = start_row + 2 + len(rows)
    sheet[end_row, 0].set_value("TABLE END")
    return end_row + 1


def _write_ods(path: Path, by_asset: dict[str, _AssetRows]) -> None:
    doc = ezodf.newdoc(doctype="ods", filename=str(path))
    for asset in sorted(by_asset):
        data = by_asset[asset]
        # 2 header rows + data + 1 TABLE END row, per table, plus 2 blank
        # separator rows between tables (mirrors RP2's own example file).
        total_rows = (2 + len(data.in_rows) + 1) + 2 + (2 + len(data.out_rows) + 1 if data.out_rows else 0) + (2 if data.out_rows else 0) + (2 + len(data.intra_rows) + 1 if data.intra_rows else 0)
        width = max(len(_IN_HEADER), len(_OUT_HEADER), len(_INTRA_HEADER))
        sheet = ezodf.Sheet(asset, size=(max(total_rows, 3), width))
        doc.sheets += sheet
        row = _write_table(sheet, 0, "IN", _IN_HEADER, data.in_rows)
        row += 1
        if data.out_rows:
            row = _write_table(sheet, row, "OUT", _OUT_HEADER, data.out_rows)
            row += 1
        if data.intra_rows:
            _write_table(sheet, row, "INTRA", _INTRA_HEADER, data.intra_rows)
    doc.save()


def _write_ini(path: Path, assets: list[str], exchanges: list[str], taxpayer_name: str) -> None:
    config = configparser.ConfigParser()
    config["in_header"] = {name: str(i) for i, name in enumerate(_IN_HEADER)}
    config["out_header"] = {name: str(i) for i, name in enumerate(_OUT_HEADER)}
    config["intra_header"] = {name: str(i) for i, name in enumerate(_INTRA_HEADER)}
    # RP2's own example config (config/crypto_example.ini) uses plain,
    # unquoted comma-separated values here despite the docs' prose showing
    # quotes — match what the real parser actually accepts.
    config["general"] = {"assets": ", ".join(assets), "exchanges": ", ".join(exchanges), "holders": taxpayer_name}
    with path.open("w") as fh:
        config.write(fh)


def parse_output(full_report_path: Path) -> list[dict]:
    """Reads the per-asset 'Impuesto'/'Tax' sheet's summary table from RP2's
    full report — scanned by header text (Año/Year in column A), not fixed
    cell coordinates, so it survives RP2's language-dependent labels.
    Returns raw rows: {year, asset, gain, term, transaction_type, cost_basis}."""
    doc = ezodf.opendoc(str(full_report_path))
    results: list[dict] = []
    for sheet in doc.sheets:
        if sheet.name in ("Leyenda", "Legend", "Resumen", "Summary") or "Entrada-Salida" in sheet.name or "In-Out" in sheet.name:
            continue
        header_row = None
        for r in range(min(10, sheet.nrows())):
            first_cell = sheet[r, 0].value
            if first_cell and str(first_cell).strip().lower() in ("año", "year"):
                header_row = r
                break
        if header_row is None:
            continue
        r = header_row + 1
        while r < sheet.nrows():
            year_cell = sheet[r, 0].value
            if year_cell is None:
                break
            try:
                year = int(year_cell)
            except (TypeError, ValueError):
                break
            results.append(
                {
                    "year": year,
                    "asset": sheet[r, 1].value,
                    "gain": sheet[r, 2].value,
                    "term": sheet[r, 3].value,
                    "transaction_type": sheet[r, 4].value,
                    "taxable_crypto": sheet[r, 5].value,
                    "taxable_fiat": sheet[r, 6].value,
                    "cost_basis": sheet[r, 7].value,
                }
            )
            r += 1
    return results
