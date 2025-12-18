#!/usr/bin/env python3
"""
Check upstream smart contract sources in qubic/core and run update_smart_contracts.py when they change.

This script looks at the latest GitHub commit that touched:
  - src/contracts/*
  - src/contract_core/contract_def.h

It stores the last observed commit SHA for each path inside data/.smart_contracts_watch.json.
If either path has a new commit, the script calls update_smart_contracts.py to refresh the local data
and then records the new SHAs. This file is meant to be triggered by cron (e.g., every Wednesday 18:00).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional

import requests

from update_smart_contracts import RAW_BASE_CONTRACTS, RAW_CONTRACT_DEF

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / ".smart_contracts_watch.json"
UPDATE_SCRIPT = ROOT / "scripts" / "update_smart_contracts.py"

REPO_OWNER = "qubic"
REPO_NAME = "core"
WATCH_PATHS: Mapping[str, str] = {
    "src/contracts": RAW_BASE_CONTRACTS,
    "src/contract_core/contract_def.h": RAW_CONTRACT_DEF,
}


def github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "smart-contract-watchdog",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def latest_commit_for_path(path: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    params = {"path": path, "per_page": 1}
    try:
        resp = requests.get(url, headers=github_headers(), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return data[0].get("sha")
    except requests.RequestException as exc:
        print(f"Warning: failed to fetch commits for {path}: {exc}")
        return None


def load_state() -> Dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"Warning: failed to parse {STATE_FILE}: {exc}")
    return {}


def save_state(data: Dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "paths": data,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_update_script() -> None:
    if not UPDATE_SCRIPT.exists():
        print(f"Error: {UPDATE_SCRIPT} not found")
        sys.exit(1)

    cmd = [sys.executable, str(UPDATE_SCRIPT)]
    print(f"Running {' '.join(cmd)} ...")
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    state = load_state()
    previous = state.get("paths", state)

    remote_shas: Dict[str, str] = {}
    changed = False

    for repo_path, raw_url in WATCH_PATHS.items():
        sha = latest_commit_for_path(repo_path)
        if not sha:
            print(f"Skipping update detection for {repo_path} ({raw_url}); no commit SHA available.")
            continue
        remote_shas[repo_path] = sha
        prev = previous.get(repo_path)
        if prev != sha:
            changed = True
            print(f"Detected change in {repo_path}: {prev or 'none'} -> {sha}")

    if not changed:
        print("No upstream changes detected; skipping update.")
        return

    try:
        run_update_script()
    except subprocess.CalledProcessError as exc:
        print(f"update_smart_contracts.py failed with exit code {exc.returncode}")
        raise

    save_state(remote_shas)
    print(f"Update complete; stored SHAs in {STATE_FILE}")


if __name__ == "__main__":
    main()
