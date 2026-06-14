#!/usr/bin/env python3
"""
Update data/smart_contracts.json by fetching sources from GitHub raw.

Rules:
- Skip contracts with contractIndex == None.
- Include contracts even with zero REGISTER_USER_PROCEDURE calls (procedures=[]).
- "name": read from contract_def.h -> struct ContractDescription -> contractDescriptions array.
          Each item (EXCEPT the first) corresponds to index 1..N.
          Use the FIRST quoted string inside each item AS-IS (no transformations).
- "label": preserved if already exists; otherwise built from filename with special Q-rule:
      * If stem starts with "Q" or "q":
          - Ensure the next char is uppercase.
          - If whole name is uppercase, keep "Q" + next uppercase + rest lowercase (QVAULT -> QVault).
          - Examples: Qx -> Qx, QUTIL -> QUtil, Qswap -> QSwap, Qbay -> QBay.
      * Otherwise: prettified phrase (GeneralQuorumProposal -> General Quorum Proposal).
- Fields: filename, name, label, githubUrl, contractIndex, address, firstUseEpoch, sharesAuctionEpoch,
          allowTransferShares, procedures(list of {id, name, fee?}).
- address: computed via Node using your JS helper:
      const publicKey = helper.getIdentityBytes(addr56);  // addr56 built from contractIndex (A..P, LE, len=56)
      const identity  = await helper.getIdentity(publicKey)  // 60-char with checksum
- Non-destructive merge: adds new contracts and new procedure IDs; preserves manual edits and custom fields.
  Authoritative fields (auto-updated from GitHub): filename, name, contractIndex, address, githubUrl,
  firstUseEpoch, sharesAuctionEpoch, allowTransferShares, procedures.
  Preserved fields: label (if already set), and any custom fields (e.g., proposalUrl, etc.).
- Sort: contracts by contractIndex; procedures by id.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------- Config ----------------------------------------

RAW_BASE_CONTRACTS = "https://raw.githubusercontent.com/qubic/core/main/src/contracts/"
RAW_CONTRACT_DEF   = "https://raw.githubusercontent.com/qubic/core/main/src/contract_core/contract_def.h"
QUBIC_STATS_API    = "https://rpc.qubic.org/v1/latest-stats"

# ---------------------------- Regexes ---------------------------------------

REGISTER_RE = re.compile(
    r"""
    REGISTER_USER_PROCEDURE
    \s*\(
        \s* [&\s]* (?P<name>[A-Za-z_][A-Za-z0-9_]*)   # 1st param (symbol), optional &
        \s*,\s*
        (?P<num>\d+)                                  # 2nd param (number)
    \s*\)
    """,
    re.VERBOSE | re.DOTALL | re.MULTILINE,
)

INCLUDE_RE = re.compile(r'#\s*include\s*["<](?P<path>[^">]+)[">]')
CONTRACT_INDEX_RE = re.compile(r'#\s*define\s+[A-Za-z0-9_]+_CONTRACT_INDEX\s+(?P<num>\d+)\b')

FIRST_QUOTED_STRING_RE = re.compile(r'"([^"]+)"')

ALLOW_TRANSFER_RE = re.compile(
    r"""
    output\s*\.\s*allowTransfer\s*=\s*(?P<value>true|false)
    """,
    re.VERBOSE,
)

PROCEDURE_DEF_RE = re.compile(
    r"PUBLIC_PROCEDURE(?:_WITH_LOCALS)?\s*\(\s*(?P<name>\w+)\s*\)",
)

CONSTEXPR_RE = re.compile(
    r"constexpr\s+\w+\s+(?P<name>\w+)\s*=\s*(?P<value>\d+)",
)

INVOCATION_REWARD_CMP_RE = re.compile(
    r"qpi\s*\.\s*invocationReward\s*\(\s*\)\s*<\s*(?P<value>(?:state\s*\.\s*(?:mut|get)\s*\(\s*\)\s*\.\s*)?\w+)",
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE  = re.compile(r"//[^\n]*")

# ---------------------------- Helpers: text/format --------------------------

def strip_comments(code: str) -> str:
    code = BLOCK_COMMENT_RE.sub("", code)
    code = LINE_COMMENT_RE.sub("", code)
    return code

def split_camel_or_snake(name: str) -> List[str]:
    if "_" in name:
        parts = name.split("_")
    else:
        # Split on camelCase boundaries while preserving acronyms:
        # - Split between lowercase and uppercase (e.g., "setting" + "CFB")
        # - Split at end of acronym before next word (e.g., "CFB" + "And")
        # - Keep single uppercase + lowercase together (e.g., "MBond" stays as one)
        parts = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z][A-Z])(?=[A-Z][a-z])", " ", name).split()
    words: List[str] = []
    for p in parts:
        # Match version patterns (v1, V2), letter sequences, or digit sequences
        words.extend(re.findall(r"[vV]\d+|[A-Za-z]+|\d+", p))
    return [w for w in words if w]

SMALL_WORDS = {
    "a","an","the","and","but","or","nor","so","yet","at","by","for","from","in","into","of","on","onto","out","over",
    "to","up","with","as","per","via","vs","vs.","off","than","till","until","past","near","down","upon","within",
    "without","through","about","before","after","around","behind","below","beneath","beside","between","beyond",
    "during","inside","outside","under","underneath","across","along","amid","among","despite","except","including",
    "like","since","toward","towards","regarding",
}

def title_with_small_words(words: List[str]) -> str:
    if not words:
        return ""
    out: List[str] = []
    last = len(words) - 1
    for i, w in enumerate(words):
        wl = w.lower()
        if w.isupper() and len(w) > 1:
            out.append(w)  # keep acronyms as-is
        elif i not in (0, last) and wl in SMALL_WORDS:
            out.append(wl)
        else:
            out.append(w.capitalize())
    return " ".join(out)

def pretty_label_from_filename(stem: str) -> str:
    return title_with_small_words(split_camel_or_snake(stem))

def pretty_procedure_name(identifier: str) -> str:
    """Format a procedure identifier into a nice phrase."""
    return title_with_small_words(split_camel_or_snake(identifier))

def label_from_filename_with_q_rule(stem: str) -> str:
    """
    Build a label from the filename stem with special 'Q*' handling:
    - If stem starts with 'Q' or 'q':
        * Ensure the NEXT char is uppercase.
        * If the whole stem is ALL CAPS (e.g., 'QVAULT'), keep 'Q' + next uppercase + rest lowercase.
          Examples: 'QUTIL' -> 'QUtil', 'QVAULT' -> 'QVault'
        * 'Qx' -> 'Qx', 'Qswap' -> 'QSwap', 'Qbay' -> 'QBay'
    - Otherwise: fall back to the pretty phrase.
    """
    if not stem:
        return ""
    if stem[0].lower() != "q" or len(stem) == 1:
        return pretty_label_from_filename(stem)

    rest = stem[1:]
    first = rest[0].upper()
    tail = rest[1:]

    if stem.isupper():
        return "Q" + first + tail.lower()

    return "Q" + first + tail

# ---------------------------- Fetch from GitHub raw -------------------------

def fetch_text(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
        print(f"Warning: GET {url} -> {resp.status_code}")
    except Exception as e:
        print(f"Warning: GET {url} failed: {e}")
    return None

def fetch_current_epoch() -> Optional[int]:
    """Fetch the current epoch from the Qubic stats API."""
    try:
        resp = requests.get(QUBIC_STATS_API, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("epoch")
        print(f"Warning: GET {QUBIC_STATS_API} -> {resp.status_code}")
    except Exception as e:
        print(f"Warning: Failed to fetch current epoch: {e}")
    return None

# ---------------------------- Parse contract_def.h --------------------------

def detect_rewritten_contracts(raw_text: str) -> set:
    """
    Detect contracts that have been rewritten by looking for *_old.h includes.
    Returns a set of base filenames (e.g., {'QVAULT.h'}) whose procedures should be
    fully replaced instead of merged.
    """
    rewritten: set = set()
    old_re = re.compile(r'#\s*include\s*["<](?:[^">]*/)?(\w+)_old\.h[">]', re.IGNORECASE)
    for m in old_re.finditer(raw_text):
        stem = m.group(1)
        # Find the actual non-old filename (case may differ)
        actual_re = re.compile(
            r'#\s*include\s*["<](?:[^">]*/)?(' + re.escape(stem) + r'\.h)[">]',
            re.IGNORECASE,
        )
        for am in actual_re.finditer(raw_text):
            fname = Path(am.group(1)).name
            if "_old" not in fname.lower():
                rewritten.add(fname)
    return rewritten

def parse_contract_def_from_raw(raw_text: str, known_contract_basenames: Optional[set] = None) -> Dict[str, int]:
    lines = raw_text.splitlines()
    mapping: Dict[str, int] = {}
    for i, line in enumerate(lines):
        inc = INCLUDE_RE.search(line)
        if not inc:
            continue
        basename = Path(inc.group("path")).name
        if known_contract_basenames and basename not in known_contract_basenames:
            continue

        cidx: Optional[int] = None
        for j in range(i - 1, max(i - 10, -1), -1):
            m = CONTRACT_INDEX_RE.search(lines[j])
            if m:
                cidx = int(m.group("num"))
                break
        if cidx is not None:
            mapping[basename] = cidx
    return mapping

def extract_contract_names_from_descriptions(raw_text: str) -> Dict[int, Dict[str, Any]]:
    """
    Extract contract info from contractDescriptions array.
    Each entry is like: {"QX", 66, 10000, sizeof(QX)}
    Returns: {contractIndex: {"name": str, "constructionEpoch": int}}
    """
    text = strip_comments(raw_text)
    token = "contractDescriptions"
    pos = text.find(token)
    if pos == -1:
        return {}
    eq_pos = text.find("=", pos)
    if eq_pos == -1:
        return {}
    brace_start = text.find("{", eq_pos)
    if brace_start == -1:
        return {}

    depth = 0
    end = brace_start
    while end < len(text):
        ch = text[end]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        end += 1
    if depth != 0:
        return {}

    body = text[brace_start + 1:end]

    items: List[str] = []
    i = 0
    while i < len(body):
        if body[i].isspace() or body[i] == ",":
            i += 1
            continue
        if body[i] != "{":
            i += 1
            continue
        start = i
        d = 0
        while i < len(body):
            if body[i] == "{":
                d += 1
            elif body[i] == "}":
                d -= 1
                if d == 0:
                    i += 1
                    items.append(body[start:i])
                    break
            i += 1

    # Regex to parse: {"NAME", constructionEpoch, destructionEpoch, sizeof(...)}
    item_re = re.compile(r'"([^"]+)"\s*,\s*(\d+)')

    contracts: Dict[int, Dict[str, Any]] = {}
    for idx1, item in enumerate(items, start=0):
        if idx1 == 0:
            continue
        m = item_re.search(item)
        if not m:
            continue
        name = m.group(1)
        construction_epoch = int(m.group(2))
        contracts[idx1] = {"name": name, "constructionEpoch": construction_epoch}
    return contracts

# ---------------------------- Header scanning -------------------------------

def should_skip_filename(fname: str) -> bool:
    if fname == "README.md":
        return True
    if fname.startswith("Test"):
        return True
    if fname in {"math_lib.h", "qpi.h"}:
        return True
    if fname.lower().endswith("_old.h"):
        return True
    return False

def _find_brace_block(text: str, start: int) -> Optional[str]:
    """Find the brace-delimited block starting at or after 'start'. Returns body including braces."""
    brace_start = text.find("{", start)
    if brace_start == -1:
        return None
    depth = 0
    end = brace_start
    while end < len(text):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:end + 1]
        end += 1
    return None

def _extract_last_arg(text: str, call_pos: int) -> Optional[str]:
    """Extract the last argument from a function call starting at call_pos (position of the '(' char)."""
    paren_start = text.find("(", call_pos)
    if paren_start == -1:
        return None
    depth = 0
    end = paren_start
    while end < len(text):
        if text[end] == "(":
            depth += 1
        elif text[end] == ")":
            depth -= 1
            if depth == 0:
                break
        end += 1
    args_str = text[paren_start + 1:end]
    # Split by commas at top-level (depth 0)
    args: List[str] = []
    current: List[str] = []
    d = 0
    for ch in args_str:
        if ch in "({":
            d += 1
        elif ch in ")}":
            d -= 1
        if ch == "," and d == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current).strip())
    return args[-1] if args else None

def _resolve_state_var_fee(text_nc: str, var_name: str) -> Optional[int]:
    """
    Resolve a state variable fee by finding its literal assignment in INITIALIZE() or BEGIN_EPOCH().
    Prefers BEGIN_EPOCH over INITIALIZE. Returns None if the assignment comes from an
    inter-contract call (CALL_OTHER_CONTRACT_FUNCTION) or is otherwise not a literal.
    """
    # Build regex for: state[.mut()].varName = <literal>
    # e.g. state.mut()._transferFee = 100 or state._transferFee = 100
    assign_re = re.compile(
        r"state\s*\.(?:\s*(?:mut|get)\s*\(\s*\)\s*\.)?\s*" + re.escape(var_name) + r"\s*=\s*(?P<value>\d+)"
    )

    for method in ["BEGIN_EPOCH", "INITIALIZE"]:
        pos = text_nc.find(method)
        if pos == -1:
            continue
        body = _find_brace_block(text_nc, pos)
        if not body:
            continue

        # If there's an inter-contract call in this method that could set the variable, skip
        if "CALL_OTHER_CONTRACT_FUNCTION" in body and var_name in body:
            continue

        # Find all assignments — use the last one (in case of overrides like "old value / new value")
        matches = list(assign_re.finditer(body))
        if matches:
            return int(matches[-1].group("value"))

    return None

def _resolve_fee_from_value(text_nc: str, value_str: str, constants: Dict[str, int]) -> Optional[int]:
    """Resolve a fee value from a literal, constant name, or state variable reference."""
    cleaned = re.sub(r'[UuLl]+$', '', value_str)
    if cleaned.isdigit():
        return int(cleaned)
    if cleaned in constants:
        return constants[cleaned]
    if cleaned.startswith("state."):
        var_name = re.sub(r'^state\.(?:(?:mut|get)\(\)\.)?', '', cleaned)
        if var_name:
            return _resolve_state_var_fee(text_nc, var_name)
    return None

FEE_PROCEDURES = {
    "transfersharemanagementrights",
    "revokeassetmanagementrights",
    "transfershareownershipandpossession",
}

def extract_procedure_fees(text: str) -> Dict[str, int]:
    """
    Extract fees from specific procedures (FEE_PROCEDURES).
    Two strategies:
    1. qpi.releaseShares() last arg — for TransferShareManagementRights, RevokeAssetManagementRights
    2. qpi.invocationReward() < X comparison — for TransferShareOwnershipAndPossession
    Returns: {procedure_name: fee_value} for procedures with resolvable fees.
    Skips procedures where the fee is dynamic or from inter-contract calls.
    """
    text_nc = strip_comments(text)

    # Collect constexpr constants
    constants: Dict[str, int] = {}
    for m in CONSTEXPR_RE.finditer(text_nc):
        constants[m.group("name")] = int(m.group("value"))

    fees: Dict[str, int] = {}
    for m in PROCEDURE_DEF_RE.finditer(text_nc):
        proc_name = m.group("name")

        # Only extract fees for the specific procedures we care about (case-insensitive)
        if proc_name.lower() not in FEE_PROCEDURES:
            continue

        body = _find_brace_block(text_nc, m.end())
        if not body:
            continue

        # Strategy 1: qpi.releaseShares() — last arg is the fee
        release_pos = body.find("qpi.releaseShares(")
        if release_pos != -1:
            last_arg = _extract_last_arg(body, release_pos)
            if last_arg is not None:
                resolved = _resolve_fee_from_value(text_nc, last_arg, constants)
                if resolved is not None:
                    fees[proc_name] = resolved
            continue

        # Strategy 2: qpi.invocationReward() < X — X is the fee
        cmp_m = INVOCATION_REWARD_CMP_RE.search(body)
        if cmp_m:
            resolved = _resolve_fee_from_value(text_nc, cmp_m.group("value"), constants)
            if resolved is not None:
                fees[proc_name] = resolved

    return fees

def extract_allow_transfer_shares(text: str) -> bool:
    """
    Extract allowTransferShares from PRE_ACQUIRE_SHARES method.
    Returns True if output.allowTransfer = true, False otherwise (including if method doesn't exist).
    """
    text_nc = strip_comments(text)
    marker = "PRE_ACQUIRE_SHARES"
    pos = text_nc.find(marker)
    if pos == -1:
        return False
    body = _find_brace_block(text_nc, pos)
    if not body:
        return False
    m = ALLOW_TRANSFER_RE.search(body)
    if m:
        return m.group("value") == "true"
    return False

def find_registers(text: str) -> List[Tuple[int, str]]:
    text_nc = strip_comments(text)
    out: List[Tuple[int, str]] = []
    for m in REGISTER_RE.finditer(text_nc):
        out.append((int(m.group("num")), m.group("name")))
    return out

# ---------------------------- Address helpers -------------------------------

def index_to_base56(idx: int) -> str:
    # Zero-based A..Z digits, little-endian; pad to 56 with 'A'
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if idx <= 0:
        return "A" * 56
    n = idx
    digits = []
    while n > 0:
        rem = n % 26
        digits.append(alphabet[rem])
        n //= 26
    s = "".join(digits)
    if len(s) > 56:
        raise ValueError("index too large for 56-char address")
    return s + "A" * (56 - len(s))


_JS_IDENTITY_PROGRAM = """
(async () => {
  const g = globalThis;
  if (typeof g.self === 'undefined') g.self = g;
  if (typeof g.window === 'undefined') g.window = g;
  if (!g.crypto || !g.crypto.subtle) {
    try { g.crypto = require('crypto').webcrypto; } catch (e) {}
  }
  if (typeof g.atob === 'undefined') g.atob = (s) => Buffer.from(s, 'base64').toString('binary');
  if (typeof g.btoa === 'undefined') g.btoa = (s) => Buffer.from(s, 'binary').toString('base64');

  const libPath = process.env.QUBIC_JS_LIB;
  const addr = process.env.QUBIC_ADDR56;
  if (!libPath || !addr) throw new Error('Missing QUBIC_JS_LIB or QUBIC_ADDR56 env vars');

  const mod = require(libPath);
  let QubicHelper = null;
  if (mod && typeof mod.QubicHelper === 'function') QubicHelper = mod.QubicHelper;
  else if (mod && mod.default && typeof mod.default.QubicHelper === 'function') QubicHelper = mod.default.QubicHelper;
  else if (typeof mod === 'function') QubicHelper = mod;
  if (!QubicHelper) throw new Error('QubicHelper class not found in exports');

  const helper = new QubicHelper();
  const publicKey = helper.getIdentityBytes(addr);
  const identity = await helper.getIdentity(publicKey);
  if (typeof identity !== 'string' || identity.length !== 60) throw new Error('Invalid identity length');
  process.stdout.write(identity);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
""".strip()

def run_js_get_identity_from_index(cidx: int, js_lib_path: Path) -> Optional[str]:
    js_path = js_lib_path.resolve()
    if not js_path.exists():
        print(f"Warning: JS helper not found at {js_path}; skipping identity.")
        return None

    addr56 = index_to_base56(cidx)
    env = {**__import__("os").environ, "QUBIC_JS_LIB": str(js_path), "QUBIC_ADDR56": addr56}

    try:
        res = subprocess.run(
            ["node", "-e", _JS_IDENTITY_PROGRAM],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=30,
        )
        return res.stdout.strip()
    except FileNotFoundError:
        print("Warning: Node not found; skipping identity.")
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() or e.stdout.strip()
        print(f"Warning: getIdentity failed: {msg}")
    return None

# ---------------------------- JSON merge/sort -------------------------------

def normalize_procs_to_list(procs: Any) -> List[Dict[str, Any]]:
    items: Dict[int, Dict[str, Any]] = {}
    if isinstance(procs, dict):
        for k, v in procs.items():
            try:
                items[int(k)] = {"id": int(k), "name": v}
            except (TypeError, ValueError):
                continue
    elif isinstance(procs, list):
        for obj in procs:
            if isinstance(obj, dict) and "id" in obj and "name" in obj:
                try:
                    entry: Dict[str, Any] = {"id": int(obj["id"]), "name": obj["name"]}
                    if "sourceIdentifier" in obj:
                        entry["sourceIdentifier"] = obj["sourceIdentifier"]
                    if "fee" in obj:
                        entry["fee"] = obj["fee"]
                    items[int(obj["id"])] = entry
                except (TypeError, ValueError):
                    continue
    return [items[pid] for pid in sorted(items.keys())]

def index_by_filename(contracts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sc in contracts:
        fn = sc.get("filename")
        if isinstance(fn, str):
            out[fn] = sc
    return out

def merge_contracts(
    existing: List[Dict[str, Any]],
    fresh: List[Dict[str, Any]],
    rewritten_filenames: Optional[set] = None,
) -> List[Dict[str, Any]]:
    by_filename: Dict[str, Dict[str, Any]] = index_by_filename(existing)
    rewritten = rewritten_filenames or set()

    for new in fresh:
        fname = new.get("filename")
        if not isinstance(fname, str):
            continue

        # Transient flag (not persisted): whether the contract source was fetched
        # successfully this run. Used to safely prune procedures removed upstream.
        source_fetched = bool(new.pop("_sourceFetched", False))

        if fname not in by_filename:
            new["procedures"] = normalize_procs_to_list(new.get("procedures", []))
            print(f"  {fname}: new contract added (index {new.get('contractIndex')}, "
                  f"{len(new['procedures'])} procedure(s))")
            existing.append(new)
            by_filename[fname] = new
            continue

        ex = by_filename[fname]

        # Update authoritative fields from fresh data
        if isinstance(new.get("name"), str):
            ex["name"] = new["name"]
        if "contractIndex" in new:
            ex["contractIndex"] = new["contractIndex"]
        if new.get("address"):
            ex["address"] = new["address"]
        if new.get("githubUrl"):
            ex["githubUrl"] = new["githubUrl"]
        if "allowTransferShares" in new:
            ex["allowTransferShares"] = new["allowTransferShares"]
        if "firstUseEpoch" in new:
            ex["firstUseEpoch"] = new["firstUseEpoch"]
        if "sharesAuctionEpoch" in new:
            ex["sharesAuctionEpoch"] = new["sharesAuctionEpoch"]

        # keep existing label if present
        if not ex.get("label") and isinstance(new.get("label"), str):
            ex["label"] = new["label"]

        # Note: All other fields in 'ex' (like proposalUrl, etc.) are preserved automatically
        # since we're updating the existing dict in-place rather than replacing it

        new_list = normalize_procs_to_list(new.get("procedures", []))

        # Rewritten contract: replace procedures entirely, but only if the
        # existing procedures don't already match (avoids re-replacing on every run
        # since the _old.h include stays in contract_def.h permanently)
        if fname in rewritten:
            ex_procs = ex.get("procedures", [])
            ex_ids = {p.get("sourceIdentifier") for p in ex_procs if isinstance(p, dict)}
            new_ids = {p.get("sourceIdentifier") for p in new_list if isinstance(p, dict)}
            if ex_ids != new_ids:
                print(f"  {fname}: rewritten contract detected, replacing procedures")
                ex["procedures"] = new_list
            continue

        # Normal merge: preserve manual edits, update what changed
        ex_procs = ex.get("procedures", [])

        # Build a map of existing procedures by id, preserving all fields
        ex_by_id: Dict[int, Dict[str, Any]] = {}
        for p in ex_procs:
            if isinstance(p, dict) and "id" in p:
                try:
                    ex_by_id[int(p["id"])] = p
                except (TypeError, ValueError):
                    continue

        merged_procs: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for new_p in new_list:
            pid = new_p["id"]
            if pid in ex_by_id:
                ex_p = ex_by_id[pid]
                # If the C++ identifier changed, update name; otherwise preserve manual edits
                if ex_p.get("sourceIdentifier") != new_p.get("sourceIdentifier"):
                    ex_p["name"] = new_p["name"]
                ex_p["sourceIdentifier"] = new_p.get("sourceIdentifier")
                # Update fee from fresh data if extracted; preserve existing fee otherwise.
                # Fees are NOT removed when absent from fresh data, because the script
                # can't always extract them (e.g. dynamic fees) and some are set manually.
                if "fee" in new_p:
                    ex_p["fee"] = new_p["fee"]
                merged_procs.append(ex_p)
            else:
                # New procedure
                print(f"  {fname}: procedure id {pid} ({new_p.get('sourceIdentifier')}) "
                      f"added from source")
                merged_procs.append(new_p)
            seen_ids.add(pid)

        # Handle existing procedures that are no longer in the fresh list.
        for pid, p in ex_by_id.items():
            if pid in seen_ids:
                continue
            # A script-generated procedure (has sourceIdentifier) that vanished from a
            # successfully-fetched source was removed upstream -> drop it. Procedures
            # without a sourceIdentifier are manual additions and are always preserved.
            # Guard on source_fetched so a transient fetch failure can't wipe procedures.
            if source_fetched and isinstance(p, dict) and p.get("sourceIdentifier"):
                print(f"  {fname}: procedure id {pid} ({p.get('sourceIdentifier')}) "
                      f"removed from source, dropping")
                continue
            merged_procs.append(p)

        merged_procs.sort(key=lambda x: x["id"])
        ex["procedures"] = merged_procs

    return existing

def sort_contracts(contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        contracts,
        key=lambda sc: sc.get("contractIndex") if sc.get("contractIndex") is not None else 1e9
    )

# ---------------------------- Main -----------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Update data/smart_contracts.json from GitHub raw (names from contract_def.h; correct identity calc; Q-label rule)."
    )
    ap.add_argument("--data-file", default="data/smart_contracts.json", help="Path to smart_contracts.json")
    ap.add_argument("--js-lib", default="lib/qubic-js-library.js", help="Path to qubic-js-library.js")
    args = ap.parse_args()

    data_path = Path(args.data_file).resolve()
    data_path.parent.mkdir(parents=True, exist_ok=True)
    js_lib_path = Path(args.js_lib).resolve()

    contract_def_text = fetch_text(RAW_CONTRACT_DEF)
    if not contract_def_text:
        raise SystemExit("Could not fetch contract_def.h")

    # Fetch current epoch to filter contracts
    current_epoch = fetch_current_epoch()
    if current_epoch is None:
        raise SystemExit("Could not fetch current epoch from API")
    print(f"Current epoch: {current_epoch}")

    stripped = strip_comments(contract_def_text)

    rewritten_filenames = detect_rewritten_contracts(contract_def_text)
    if rewritten_filenames:
        print(f"Rewritten contracts detected: {rewritten_filenames}")

    all_basenames = set(Path(m.group("path")).name for m in INCLUDE_RE.finditer(stripped))
    basenames = {b for b in all_basenames if not should_skip_filename(b)}
    idx_map = parse_contract_def_from_raw(stripped, basenames)

    idx_to_info = extract_contract_names_from_descriptions(stripped)

    fresh_entries: List[Dict[str, Any]] = []
    for basename in sorted(basenames):
        cidx = idx_map.get(basename)
        if cidx is None:
            continue

        # Skip contracts whose shares auction hasn't started yet
        contract_info = idx_to_info.get(cidx, {})
        construction_epoch = contract_info.get("constructionEpoch")
        if current_epoch is not None and construction_epoch is not None:
            shares_auction_epoch = construction_epoch - 1
            if shares_auction_epoch > current_epoch:
                print(f"Skipping {basename}: sharesAuctionEpoch {shares_auction_epoch} > current epoch {current_epoch}")
                continue

        url = RAW_BASE_CONTRACTS + basename
        text = fetch_text(url)

        regs: List[Tuple[int, str]] = []
        allow_transfer_shares = False
        proc_fees: Dict[str, int] = {}
        if text:
            regs = find_registers(text)
            allow_transfer_shares = extract_allow_transfer_shares(text)
            proc_fees = extract_procedure_fees(text)

        procs: List[Dict[str, Any]] = []
        seen: set[int] = set()
        for num, ident in regs:
            if num in seen:
                continue
            seen.add(num)
            pretty_name = pretty_procedure_name(ident)
            proc_entry: Dict[str, Any] = {"id": num, "name": pretty_name, "sourceIdentifier": ident}
            if ident in proc_fees:
                proc_entry["fee"] = proc_fees[ident]
            procs.append(proc_entry)
        procs.sort(key=lambda x: x["id"])

        stem = Path(basename).stem
        label = label_from_filename_with_q_rule(stem)

        name_value = contract_info.get("name", stem.upper())

        addr: Optional[str] = None
        identity = run_js_get_identity_from_index(cidx, js_lib_path)
        if identity and len(identity) == 60:
            addr = identity
        else:
            addr = index_to_base56(cidx)

        githubUrl = f"https://github.com/qubic/core/blob/main/src/contracts/{basename}"

        entry: Dict[str, Any] = {
            "filename": basename,
            "name": name_value,
            "label": label,
            "githubUrl": githubUrl,
            "contractIndex": cidx,
            "address": addr,
            "allowTransferShares": allow_transfer_shares,
            "procedures": procs,
            # Transient (popped during merge): did we fetch the source this run?
            "_sourceFetched": text is not None,
        }

        if construction_epoch is not None:
            entry["firstUseEpoch"] = construction_epoch
            entry["sharesAuctionEpoch"] = construction_epoch - 1

        fresh_entries.append(entry)

    try:
        existing_top = json.loads(data_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing_top = {}
    except Exception as e:
        raise SystemExit(f"Error reading {data_path}: {e}")
    if not isinstance(existing_top, dict):
        raise SystemExit(f"Error: {data_path} does not contain a JSON object")

    existing_sc = existing_top.get("smart_contracts", [])
    if not isinstance(existing_sc, list):
        existing_sc = []

    merged_sc = merge_contracts(existing_sc, fresh_entries, rewritten_filenames)
    merged_sc = sort_contracts(merged_sc)

    existing_top["smart_contracts"] = merged_sc

    data_path.write_text(json.dumps(existing_top, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated {data_path} with {len(merged_sc)} smart_contract(s).")

# ----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
