import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse JSON: {path} ({exc})") from exc


def validate_qbi_file(path: Path) -> List[str]:
    errors: List[str] = []
    data = load_json(path)
    if not isinstance(data, dict):
        return [f"{path}: top-level must be an object"]
    if data.get("qbiVersion") != "0.1":
        errors.append(f"{path}: qbiVersion must be '0.1'")
    contract = data.get("contract")
    if not isinstance(contract, dict):
        errors.append(f"{path}: contract must be an object")
    else:
        name = contract.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{path}: contract.name must be a non-empty string")
        idx = contract.get("contractIndex")
        if idx is not None and (not isinstance(idx, int) or idx < 0):
            errors.append(f"{path}: contract.contractIndex must be an integer >= 0")
    entries = data.get("entries")
    if not isinstance(entries, list):
        errors.append(f"{path}: entries must be a list")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate QBI registry files in static repo.")
    parser.add_argument("--root", default=".", help="Path to static repo root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    smart_path = root / "data" / "smart_contracts.json"
    index_path = root / "data" / "qbi" / "index.json"
    registry_dir = root / "data" / "qbi" / "registry"

    errors: List[str] = []

    if not smart_path.exists():
        errors.append(f"Missing {smart_path}")
    if not index_path.exists():
        errors.append(f"Missing {index_path}")
    if not registry_dir.exists():
        errors.append(f"Missing {registry_dir}")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    smart = load_json(smart_path)
    index = load_json(index_path)

    smart_contracts = smart.get("smart_contracts", [])
    if not isinstance(smart_contracts, list):
        print("smart_contracts must be a list", file=sys.stderr)
        return 1

    index_contracts = index.get("contracts", [])
    if not isinstance(index_contracts, list):
        print("data/qbi/index.json contracts must be a list", file=sys.stderr)
        return 1

    index_map = {c.get("filename"): c for c in index_contracts if isinstance(c, dict)}

    for sc in smart_contracts:
        if not isinstance(sc, dict):
            errors.append("smart_contracts entry must be an object")
            continue
        filename = sc.get("filename")
        qbi_file = sc.get("qbiFile")
        if not filename or not qbi_file:
            errors.append(f"Missing filename/qbiFile in smart_contracts entry: {sc}")
            continue
        expected = f"{Path(filename).stem}.json"
        if qbi_file != expected:
            errors.append(f"{filename}: qbiFile should be {expected}, got {qbi_file}")
        qbi_path = registry_dir / qbi_file
        if not qbi_path.exists():
            errors.append(f"Missing QBI file: {qbi_path}")
            continue
        errors.extend(validate_qbi_file(qbi_path))
        idx_entry = index_map.get(filename)
        if not idx_entry:
            errors.append(f"Missing index entry for {filename}")
        else:
            if idx_entry.get("qbiFile") != qbi_file:
                errors.append(f"Index mismatch for {filename}: qbiFile differs")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    print("QBI registry validation ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
