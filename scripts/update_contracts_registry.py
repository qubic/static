#!/usr/bin/env python3
"""Refresh data/contracts_registry.json from qubic.ts generated source."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

RAW_SOURCE_URL = (
    "https://raw.githubusercontent.com/qubic/qubic.ts/main/"
    "packages/contracts/src/generated/core-registry.source.json"
)
API_SOURCE_URL = (
    "https://api.github.com/repos/qubic/qubic.ts/contents/"
    "packages/contracts/src/generated/core-registry.source.json?ref=main"
)


def fetch_json(url: str, token: str | None = None, raw_accept: bool = False) -> Any:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if raw_accept:
        headers["Accept"] = "application/vnd.github.raw+json"

    response = requests.get(url, timeout=30, headers=headers)
    response.raise_for_status()
    return response.json()

def fetch_registry_payload(source_url: str | None) -> Any:
    token = os.environ.get("QUBIC_TS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")

    if source_url:
        return fetch_json(source_url, token=token, raw_accept="api.github.com" in source_url)

    attempts: list[tuple[str, str | None, bool]] = []
    if token:
        attempts.append((API_SOURCE_URL, token, True))
    attempts.append((RAW_SOURCE_URL, None, False))

    errors: list[str] = []
    for url, auth_token, raw_accept in attempts:
        try:
            return fetch_json(url, token=auth_token, raw_accept=raw_accept)
        except Exception as error:  # pragma: no cover - best-effort multi-source fetch
            errors.append(f"{url}: {error}")

    raise RuntimeError("Unable to fetch contracts registry source:\n" + "\n".join(errors))


def validate_registry_shape(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("registry payload must be an object")
    if value.get("version") != 1:
        raise ValueError("registry.version must be 1")
    if not isinstance(value.get("metadata"), dict):
        raise ValueError("registry.metadata must be an object")
    contracts = value.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("registry.contracts must be an array")


def write_registry(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh data/contracts_registry.json")
    parser.add_argument(
        "--source-url",
        default=None,
        help="Optional explicit source URL. Defaults to token-aware multi-source fetch.",
    )
    parser.add_argument(
        "--output",
        default="data/contracts_registry.json",
        help="Output file path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = fetch_registry_payload(args.source_url)
    validate_registry_shape(payload)

    output_path = Path(args.output)
    write_registry(output_path, payload)

    contracts = payload.get("contracts", [])
    generated_at = payload.get("metadata", {}).get("generatedAt", "unknown")
    print(
        f"Updated {output_path} "
        f"(contracts={len(contracts)}, generatedAt={generated_at})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
