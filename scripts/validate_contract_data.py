#!/usr/bin/env python3
"""Validate contract data files that are owned by the static repository.

The script intentionally uses only the Python standard library so it can run in
local development and GitHub Actions without an extra dependency install. It
performs semantic checks that JSON Schema alone cannot express, and it validates
that the checked-in schema files are parseable JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path} does not exist")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def require(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)


def validate_schema_files(base_dir: Path, errors: list[str]) -> None:
    schema_dir = base_dir / "data" / "contracts" / "schemas"
    require(schema_dir.exists(), errors, f"{schema_dir} does not exist")
    if not schema_dir.exists():
        return

    schema_files = sorted(schema_dir.glob("*.schema.json"))
    require(len(schema_files) > 0, errors, f"{schema_dir} has no schema files")
    for path in schema_files:
        schema = load_json(path)
        require(isinstance(schema, dict), errors, f"{path} must contain a JSON object")
        require("$schema" in schema, errors, f"{path} is missing $schema")
        require("title" in schema, errors, f"{path} is missing title")


def validate_smart_contracts(base_dir: Path, errors: list[str]) -> None:
    path = base_dir / "data" / "smart_contracts.json"
    data = load_json(path)
    require(isinstance(data, dict), errors, f"{path} must contain a JSON object")
    contracts = data.get("smart_contracts") if isinstance(data, dict) else None
    require(isinstance(contracts, list), errors, "smart_contracts must be a list")
    if not isinstance(contracts, list):
        return

    seen_indexes: set[int] = set()
    seen_filenames: set[str] = set()

    for index, contract in enumerate(contracts):
        prefix = f"smart_contracts[{index}]"
        require(isinstance(contract, dict), errors, f"{prefix} must be an object")
        if not isinstance(contract, dict):
            continue

        filename = contract.get("filename")
        contract_index = contract.get("contractIndex")
        procedures = contract.get("procedures")
        first_use_epoch = contract.get("firstUseEpoch")
        shares_auction_epoch = contract.get("sharesAuctionEpoch")

        require(isinstance(filename, str) and filename.endswith(".h"), errors, f"{prefix}.filename must be a header filename")
        require(isinstance(contract.get("name"), str) and contract["name"], errors, f"{prefix}.name must be a non-empty string")
        require(isinstance(contract.get("label"), str) and contract["label"], errors, f"{prefix}.label must be a non-empty string")
        require(isinstance(contract.get("githubUrl"), str) and contract["githubUrl"].startswith("https://"), errors, f"{prefix}.githubUrl must be an https URL")
        require(isinstance(contract_index, int) and contract_index > 0, errors, f"{prefix}.contractIndex must be a positive integer")
        require(isinstance(contract.get("address"), str) and 56 <= len(contract["address"]) <= 60, errors, f"{prefix}.address must be 56 to 60 characters")
        require(isinstance(contract.get("allowTransferShares"), bool), errors, f"{prefix}.allowTransferShares must be a boolean")
        require(isinstance(first_use_epoch, int) and first_use_epoch >= 0, errors, f"{prefix}.firstUseEpoch must be a non-negative integer")
        require(isinstance(shares_auction_epoch, int) and shares_auction_epoch >= 0, errors, f"{prefix}.sharesAuctionEpoch must be a non-negative integer")
        if isinstance(first_use_epoch, int) and isinstance(shares_auction_epoch, int):
            require(shares_auction_epoch == first_use_epoch - 1, errors, f"{prefix}.sharesAuctionEpoch must equal firstUseEpoch - 1")

        if isinstance(contract_index, int):
            require(contract_index not in seen_indexes, errors, f"duplicate contractIndex {contract_index}")
            seen_indexes.add(contract_index)
        if isinstance(filename, str):
            require(filename not in seen_filenames, errors, f"duplicate filename {filename}")
            seen_filenames.add(filename)

        require(isinstance(procedures, list), errors, f"{prefix}.procedures must be a list")
        if not isinstance(procedures, list):
            continue

        seen_procedure_ids: set[int] = set()
        for procedure_index, procedure in enumerate(procedures):
            procedure_prefix = f"{prefix}.procedures[{procedure_index}]"
            require(isinstance(procedure, dict), errors, f"{procedure_prefix} must be an object")
            if not isinstance(procedure, dict):
                continue
            procedure_id = procedure.get("id")
            require(isinstance(procedure_id, int) and procedure_id >= 0, errors, f"{procedure_prefix}.id must be a non-negative integer")
            require(isinstance(procedure.get("name"), str) and procedure["name"], errors, f"{procedure_prefix}.name must be a non-empty string")
            if isinstance(procedure_id, int):
                require(procedure_id not in seen_procedure_ids, errors, f"{prefix} has duplicate procedure id {procedure_id}")
                seen_procedure_ids.add(procedure_id)
            if "sourceIdentifier" in procedure:
                require(isinstance(procedure["sourceIdentifier"], str) and procedure["sourceIdentifier"], errors, f"{procedure_prefix}.sourceIdentifier must be a non-empty string")
            if "fee" in procedure:
                require(isinstance(procedure["fee"], int) and procedure["fee"] >= 0, errors, f"{procedure_prefix}.fee must be a non-negative integer")


def validate_contract_registry(base_dir: Path, errors: list[str]) -> None:
    path = base_dir / "data" / "contracts" / "registry.json"
    if not path.exists():
        return

    data = load_json(path)
    require(isinstance(data, dict), errors, f"{path} must contain a JSON object")
    versions = data.get("versions") if isinstance(data, dict) else None
    require(isinstance(versions, list), errors, f"{path}.versions must be a list")
    if not isinstance(versions, list):
        return

    current_by_contract: dict[int, int] = {}
    ranges_by_contract: dict[int, list[tuple[int, int | None]]] = {}

    for index, version in enumerate(versions):
        prefix = f"contracts.registry.versions[{index}]"
        require(isinstance(version, dict), errors, f"{prefix} must be an object")
        if not isinstance(version, dict):
            continue
        contract_index = version.get("contractIndex")
        from_epoch = version.get("effectiveFromEpoch")
        to_epoch = version.get("effectiveToEpoch")
        require(isinstance(contract_index, int) and contract_index > 0, errors, f"{prefix}.contractIndex must be a positive integer")
        require(isinstance(from_epoch, int) and from_epoch >= 0, errors, f"{prefix}.effectiveFromEpoch must be a non-negative integer")
        require(to_epoch is None or isinstance(to_epoch, int), errors, f"{prefix}.effectiveToEpoch must be an integer or null")
        if isinstance(from_epoch, int) and isinstance(to_epoch, int):
            require(to_epoch >= from_epoch, errors, f"{prefix}.effectiveToEpoch must be greater than or equal to effectiveFromEpoch")
        if isinstance(contract_index, int):
            ranges_by_contract.setdefault(contract_index, []).append((from_epoch, to_epoch))
            if to_epoch is None:
                current_by_contract[contract_index] = current_by_contract.get(contract_index, 0) + 1

    for contract_index, count in current_by_contract.items():
        require(count == 1, errors, f"contract {contract_index} must have exactly one current ABI version")

    for contract_index, ranges in ranges_by_contract.items():
        sortable_ranges = [r for r in ranges if isinstance(r[0], int)]
        sortable_ranges.sort(key=lambda item: item[0])
        previous_to: int | None = None
        for from_epoch, to_epoch in sortable_ranges:
            if previous_to is not None:
                require(from_epoch > previous_to, errors, f"contract {contract_index} has overlapping ABI ranges")
            if to_epoch is not None:
                previous_to = to_epoch


def validate_manifest(base_dir: Path, errors: list[str]) -> None:
    path = base_dir / "data" / "contracts" / "manifest.json"
    if not path.exists():
        return

    data = load_json(path)
    require(isinstance(data, dict), errors, f"{path} must contain a JSON object")
    files = data.get("files") if isinstance(data, dict) else None
    require(isinstance(files, dict), errors, f"{path}.files must be an object")
    if not isinstance(files, dict):
        return

    for file_name, metadata in files.items():
        prefix = f"manifest.files.{file_name}"
        require(isinstance(metadata, dict), errors, f"{prefix} must be an object")
        if not isinstance(metadata, dict):
            continue
        require(isinstance(metadata.get("schema"), str) and metadata["schema"], errors, f"{prefix}.schema must be a non-empty string")
        require(isinstance(metadata.get("hash"), str) and HASH_RE.match(metadata["hash"]) is not None, errors, f"{prefix}.hash must be a sha256 hash")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Qubic static contract data")
    parser.add_argument("--base-dir", default=".", help="Repository root to validate")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    errors: list[str] = []

    for validator in (
        validate_schema_files,
        validate_smart_contracts,
        validate_contract_registry,
        validate_manifest,
    ):
        try:
            validator(base_dir, errors)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        print("Contract data validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Contract data validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
