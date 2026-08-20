from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_TIMEOUT_SECONDS = 120


class RP2Error(Exception):
    """RP2 ran but failed, or isn't installed. Message includes RP2's own
    stderr so a real failure reason reaches the user, not just 'it broke'."""


def _resolve(executable: str) -> str:
    """Console scripts installed by pip land next to the interpreter's own
    binary (venv/bin/), which isn't necessarily on PATH unless the venv is
    'activated' in the shell sense — check there first, then fall back to
    a plain PATH lookup for non-venv setups."""
    beside_python = Path(sys.executable).parent / executable
    if beside_python.exists():
        return str(beside_python)
    found = shutil.which(executable)
    if found:
        return found
    raise RP2Error(f"'{executable}' isn't installed in this environment — check that the 'rp2' package installed correctly.")


def run(executable: str, config_path: Path, input_path: Path, output_dir: Path, method: str, prefix: str = "report_") -> Path:
    """Invokes the installed RP2 country executable (e.g. rp2_es) as a
    subprocess — RP2 is a CLI tool, not a library we import (plan §56/§59:
    'Canonical Ledger -> RP2 adapter -> RP2 -> structured results -> our UI').
    Returns the path to the full-report ODS file it generated."""
    resolved = _resolve(executable)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [resolved, "-n", "-m", method, "-o", str(output_dir), "-p", prefix, str(config_path), str(input_path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RP2Error(f"{executable} did not finish within {_TIMEOUT_SECONDS}s.") from exc
    if result.returncode != 0:
        raise RP2Error(f"{executable} exited with an error:\n{result.stderr.strip() or result.stdout.strip()}")

    full_report = next(output_dir.glob(f"{prefix}*rp2_full_report.ods"), None)
    if full_report is None:
        raise RP2Error(f"{executable} completed but did not produce a rp2_full_report.ods file.\n{result.stdout}")
    return full_report
