import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ContractDefEntry:
    name: str
    contract_index: int
    header: str


def parse_contract_def_h(source: str) -> List[ContractDefEntry]:
    lines = source.splitlines()
    entries: List[ContractDefEntry] = []
    current_index: Optional[int] = None
    current_state_type: Optional[str] = None
    current_header: Optional[str] = None

    def flush() -> None:
        nonlocal current_index, current_state_type, current_header
        if current_index is not None and current_state_type and current_header:
            entries.append(
                ContractDefEntry(
                    name=current_state_type,
                    contract_index=current_index,
                    header=current_header,
                )
            )
        current_index = None
        current_state_type = None
        current_header = None

    for line in lines:
        idx = re.match(r"^\s*#define\s+([A-Z0-9_]+)_CONTRACT_INDEX\s+(\d+)\s*$", line)
        if idx:
            flush()
            current_index = int(idx.group(2))
            continue
        st = re.match(r"^\s*#define\s+CONTRACT_STATE_TYPE\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if st:
            current_state_type = st.group(1)
            continue
        inc = re.match(r'^\s*#include\s+"contracts/([^"]+)"\s*$', line)
        if inc:
            current_header = inc.group(1)
            continue

    flush()
    return entries


def extract_registered_entries(source: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    func_re = re.compile(r"REGISTER_USER_FUNCTION\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(\d+)\s*\)\s*;")
    proc_re = re.compile(r"REGISTER_USER_PROCEDURE\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(\d+)\s*\)\s*;")
    for match in func_re.finditer(source):
        out.append({"kind": "function", "name": match.group(1), "inputType": int(match.group(2))})
    for match in proc_re.finditer(source):
        out.append({"kind": "procedure", "name": match.group(1), "inputType": int(match.group(2))})
    return out


def strip_comments(source: str) -> str:
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    source = re.sub(r"//.*$", "", source, flags=re.MULTILINE)
    return source


def extract_struct_body(source: str, struct_name: str) -> Optional[str]:
    idx = source.find(f"struct {struct_name}")
    if idx == -1:
        return None
    brace_start = source.find("{", idx)
    if brace_start == -1:
        return None
    depth = 0
    for i in range(brace_start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start + 1 : i]
    return None


def extract_type_aliases(source: str) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    typedef_re = re.compile(r"^\s*typedef\s+([^;]+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$", re.MULTILINE)
    for match in typedef_re.finditer(source):
        aliases[match.group(2).strip()] = match.group(1).strip()
    using_re = re.compile(r"^\s*using\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+?)\s*;\s*$", re.MULTILINE)
    for match in using_re.finditer(source):
        aliases[match.group(1).strip()] = match.group(2).strip()
    return aliases


def tokenize_numeric_expr(expr: str) -> Optional[List[Any]]:
    out: List[Any] = []
    s = re.sub(r"\bULL\b|\bLL\b|\bUL\b|\bL\b|\bU\b", "", expr)
    s = re.sub(r"\s+", " ", s).strip()
    if re.search(r"[/%]|div\s*\(|mod\s*\(", s):
        return None
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == " ":
            i += 1
            continue
        if ch in ("(", ")"):
            out.append({"type": "paren", "value": ch})
            i += 1
            continue
        if ch in ("+", "-", "*"):
            out.append({"type": "op", "value": ch})
            i += 1
            continue
        if ch.isdigit():
            j = i + 1
            while j < len(s) and s[j].isdigit():
                j += 1
            out.append(int(s[i:j]))
            i = j
            continue
        if re.match(r"[A-Za-z_]", ch):
            j = i + 1
            while j < len(s) and re.match(r"[A-Za-z0-9_]", s[j]):
                j += 1
            out.append({"type": "ident", "value": s[i:j]})
            i = j
            continue
        return None
    return out


def parse_numeric_expr(expr: str, constants: Dict[str, int]) -> Optional[int]:
    tokens = tokenize_numeric_expr(expr)
    if not tokens:
        return None
    output: List[Any] = []
    ops: List[str] = []
    prec = {"+": 1, "-": 1, "*": 2}
    for t in tokens:
        if isinstance(t, int):
            output.append(t)
            continue
        if t["type"] == "ident":
            val = constants.get(t["value"])
            if val is None:
                return None
            output.append(val)
            continue
        if t["type"] == "op":
            while ops:
                top = ops[-1]
                if top == "(":
                    break
                if prec[top] >= prec[t["value"]]:
                    output.append(ops.pop())
                else:
                    break
            ops.append(t["value"])
            continue
        if t["type"] == "paren":
            if t["value"] == "(":
                ops.append("(")
            else:
                while ops and ops[-1] != "(":
                    output.append(ops.pop())
                if not ops:
                    return None
                ops.pop()
    while ops:
        op = ops.pop()
        if op == "(":
            return None
        output.append(op)
    stack: List[int] = []
    for item in output:
        if isinstance(item, int):
            stack.append(item)
            continue
        b = stack.pop() if stack else None
        a = stack.pop() if stack else None
        if a is None or b is None:
            return None
        if item == "+":
            stack.append(a + b)
        elif item == "-":
            stack.append(a - b)
        elif item == "*":
            stack.append(a * b)
        else:
            return None
    if len(stack) != 1:
        return None
    return stack[0]


def extract_numeric_constants(source: str) -> Dict[str, int]:
    constants: Dict[str, int] = {"X_MULTIPLIER": 1}
    constexpr_re = re.compile(r"constexpr\s+[A-Za-z0-9_:<>]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);")
    for match in constexpr_re.finditer(source):
        name = match.group(1)
        expr = match.group(2).strip()
        val = parse_numeric_expr(expr, constants)
        if val is not None:
            constants[name] = val
    define_re = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$", re.MULTILINE)
    for match in define_re.finditer(source):
        name = match.group(1)
        expr = match.group(2).strip()
        val = parse_numeric_expr(expr, constants)
        if val is not None:
            constants[name] = val
    return constants


def parse_len(token: str, constants: Dict[str, int]) -> Optional[int]:
    if re.match(r"^\d+$", token):
        n = int(token)
    else:
        n = constants.get(token)
    if n is None or n < 0:
        return None
    return n


def parse_struct_fields(
    body: str,
    constants: Dict[str, int],
    aliases: Dict[str, str],
    header_source: str,
    struct_cache: Dict[str, Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    fields: List[Dict[str, Any]] = []
    statements = (
        strip_comments(body)
        .replace("\r\n", " ")
        .replace("\n", " ")
        .split(";")
    )
    statements = [s.strip() for s in statements if s.strip()]
    for stmt in statements:
        if re.match(r"^[{}]+$", stmt):
            continue
        if "(" in stmt:
            continue
        if re.match(r"^\s*(struct|class|enum|union)\b", stmt):
            continue
        if re.match(r"^\s*#", stmt):
            continue

        arr = re.match(r"^(.+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([A-Za-z0-9_]+)\s*]\s*$", stmt)
        if arr:
            raw_type = arr.group(1).strip()
            name = arr.group(2)
            len_token = arr.group(3)
            length = parse_len(len_token, constants)
            if length is None:
                warnings.append(f"Could not resolve array length token: {len_token}")
                continue
            item = parse_type_ref(raw_type, constants, aliases, header_source, struct_cache, warnings)
            fields.append({"name": name, "typeRef": {"type": "array", "length": length, "item": item}})
            continue

        decl = re.match(r"^(.+?)\s+([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*$", stmt)
        if decl:
            raw_type = decl.group(1).strip()
            raw_names = [n.strip() for n in decl.group(2).split(",")]
            type_ref = parse_type_ref(raw_type, constants, aliases, header_source, struct_cache, warnings)
            for name in raw_names:
                fields.append({"name": name, "typeRef": type_ref})
            continue

        warnings.append(f"Unrecognized field statement: {stmt}")
    return {"type": "struct", "fields": fields}


def parse_type_ref(
    raw_type: str,
    constants: Dict[str, int],
    aliases: Dict[str, str],
    header_source: str,
    struct_cache: Dict[str, Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    t = raw_type.replace("const ", "").strip()
    if t.endswith("&"):
        t = t[:-1].strip()
    t = re.sub(r"^QPI::", "", t)

    array_match = re.match(r"^Array\s*<\s*([^,>]+)\s*,\s*([^>]+)\s*>\s*$", t)
    if array_match:
        item_type = array_match.group(1).strip()
        len_token = array_match.group(2).strip()
        length = parse_len(len_token, constants)
        if length is None:
            warnings.append(f"Could not resolve Array length token: {len_token}")
            return {"type": "bytes", "length": 0}
        return {"type": "array", "length": length, "item": parse_type_ref(item_type, constants, aliases, header_source, struct_cache, warnings)}

    bit_array = re.match(r"^BitArray\s*<\s*([^>]+)\s*>\s*$", t)
    if bit_array:
        len_token = bit_array.group(1).strip()
        bits = parse_len(len_token, constants)
        if bits is None:
            warnings.append(f"Could not resolve BitArray length token: {len_token}")
            return {"type": "bytes", "length": 0}
        elements = max(1, (bits + 63) // 64)
        return {"type": "array", "length": elements, "item": {"type": "u64"}}

    alias_match = re.match(r"^(sint|uint)(8|16|32|64)_(\d+)$", t)
    if alias_match:
        signedness, width, length = alias_match.group(1), alias_match.group(2), int(alias_match.group(3))
        if length >= 0:
            item_type = f'{"u" if signedness == "uint" else "i"}{width}'
            return {"type": "array", "length": length, "item": {"type": item_type}}

    id_alias = re.match(r"^id_(\d+)$", t)
    if id_alias:
        length = int(id_alias.group(1))
        if length >= 0:
            return {"type": "array", "length": length, "item": {"type": "m256i"}}

    bit_alias = re.match(r"^bit_(\d+)$", t)
    if bit_alias:
        bits = int(bit_alias.group(1))
        if bits >= 0:
            elements = max(1, (bits + 63) // 64)
            return {"type": "array", "length": elements, "item": {"type": "u64"}}

    primitives = {
        "uint8": "u8",
        "unsigned char": "u8",
        "sint8": "i8",
        "signed char": "i8",
        "uint16": "u16",
        "unsigned short": "u16",
        "sint16": "i16",
        "signed short": "i16",
        "uint32": "u32",
        "unsigned int": "u32",
        "sint32": "i32",
        "signed int": "i32",
        "uint64": "u64",
        "unsigned long long": "u64",
        "sint64": "i64",
        "signed long long": "i64",
        "long long": "i64",
        "bool": "bool",
        "bit": "bool",
        "id": "m256i",
        "m256i": "m256i",
    }
    if t in primitives:
        return {"type": primitives[t]}

    if t == "Asset":
        return {
            "type": "struct",
            "fields": [
                {"name": "issuer", "typeRef": {"type": "m256i"}},
                {"name": "assetName", "typeRef": {"type": "u64"}},
            ],
        }

    if t == "ProposalSingleVoteDataV1":
        return {
            "type": "struct",
            "fields": [
                {"name": "proposalIndex", "typeRef": {"type": "u16"}},
                {"name": "proposalType", "typeRef": {"type": "u16"}},
                {"name": "proposalTick", "typeRef": {"type": "u32"}},
                {"name": "voteValue", "typeRef": {"type": "i64"}},
            ],
        }

    if t == "ProposalMultiVoteDataV1":
        return {
            "type": "struct",
            "fields": [
                {"name": "proposalIndex", "typeRef": {"type": "u16"}},
                {"name": "proposalType", "typeRef": {"type": "u16"}},
                {"name": "proposalTick", "typeRef": {"type": "u32"}},
                {"name": "voteValues", "typeRef": {"type": "array", "length": 8, "item": {"type": "i64"}}},
                {"name": "voteCounts", "typeRef": {"type": "array", "length": 8, "item": {"type": "u32"}}},
            ],
        }

    if t == "ProposalSummarizedVotingDataV1":
        return {
            "type": "struct",
            "fields": [
                {"name": "proposalIndex", "typeRef": {"type": "u16"}},
                {"name": "optionCount", "typeRef": {"type": "u16"}},
                {"name": "proposalTick", "typeRef": {"type": "u32"}},
                {"name": "totalVotesAuthorized", "typeRef": {"type": "u32"}},
                {"name": "totalVotesCasted", "typeRef": {"type": "u32"}},
                {"name": "resultBytes", "typeRef": {"type": "bytes", "length": 32}},
            ],
        }

    if t == "ProposalDataYesNo":
        return {"type": "bytes", "length": 304}

    proposal_match = re.match(r"^ProposalDataV1\s*<\s*(true|false)\s*>\s*$", t)
    if proposal_match:
        return {"type": "bytes", "length": 328}

    aliased = aliases.get(t)
    if aliased and aliased != t:
        return parse_type_ref(aliased, constants, aliases, header_source, struct_cache, warnings)

    cached = struct_cache.get(t)
    if cached:
        return cached
    body = extract_struct_body(header_source, t)
    if body:
        ref = parse_struct_fields(body, constants, aliases, header_source, struct_cache, warnings)
        struct_cache[t] = ref
        return ref

    warnings.append(f"Unknown type: {t} (treated as bytes[0])")
    return {"type": "bytes", "length": 0}


def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return ((value + alignment - 1) // alignment) * alignment


def cpp_layout_of(type_ref: Dict[str, Any]) -> Dict[str, int]:
    t = type_ref["type"]
    if t == "nodata":
        expected = type_ref.get("expectedSize", 1)
        return {"size": max(1, int(expected)), "align": 1}
    if t in ("bool", "u8", "i8"):
        return {"size": 1, "align": 1}
    if t in ("u16", "i16"):
        return {"size": 2, "align": 2}
    if t in ("u32", "i32"):
        return {"size": 4, "align": 4}
    if t in ("u64", "i64"):
        return {"size": 8, "align": 8}
    if t == "bytes":
        length = int(type_ref["length"])
        return {"size": length, "align": 1}
    if t == "m256i":
        return {"size": 32, "align": 8}
    if t == "array":
        length = int(type_ref["length"])
        item = cpp_layout_of(type_ref["item"])
        return {"size": item["size"] * length, "align": item["align"]}
    if t == "struct":
        offset = 0
        struct_align = 1
        for field in type_ref["fields"]:
            layout = cpp_layout_of(field["typeRef"])
            offset = align_up(offset, layout["align"])
            offset += layout["size"]
            struct_align = max(struct_align, layout["align"])
        size = align_up(offset, struct_align)
        return {"size": 1 if size == 0 else size, "align": struct_align}
    raise ValueError(f"unknown type: {t}")


def normalize_no_data(type_ref: Dict[str, Any], expected_size: int) -> Dict[str, Any]:
    if type_ref["type"] != "struct":
        return type_ref
    if type_ref["fields"]:
        return type_ref
    return {"type": "nodata", "expectedSize": expected_size}


def compile_contract_header(header_source: str, contract_name: str, contract_index: Optional[int]) -> Dict[str, Any]:
    warnings: List[str] = []
    constants = extract_numeric_constants(header_source)
    aliases = extract_type_aliases(header_source)
    entries = extract_registered_entries(header_source)
    struct_cache: Dict[str, Dict[str, Any]] = {}
    compiled: List[Dict[str, Any]] = []

    for entry in entries:
        input_struct_name = f"{entry['name']}_input"
        output_struct_name = f"{entry['name']}_output"
        input_struct = extract_struct_body(header_source, input_struct_name)
        output_struct = extract_struct_body(header_source, output_struct_name)

        if input_struct:
            input_type = parse_struct_fields(input_struct, constants, aliases, header_source, struct_cache, warnings)
        else:
            input_type = {"type": "nodata"}
        if output_struct:
            output_type = parse_struct_fields(output_struct, constants, aliases, header_source, struct_cache, warnings)
        else:
            output_type = {"type": "nodata"}

        input_layout = cpp_layout_of(input_type)
        output_layout = cpp_layout_of(output_type)

        compiled.append(
            {
                "kind": entry["kind"],
                "name": entry["name"],
                "inputType": entry["inputType"],
                "input": normalize_no_data(input_type, input_layout["size"]),
                "output": normalize_no_data(output_type, output_layout["size"]),
                "inputSize": input_layout["size"],
                "outputSize": output_layout["size"],
            }
        )

    compiled.sort(key=lambda e: (e["inputType"], e["name"]))

    if warnings:
        # Keep warnings for debugging in logs, but do not emit them into output JSON.
        for w in warnings:
            pass

    return {
        "qbiVersion": "0.1",
        "contract": {"name": contract_name, "contractIndex": contract_index},
        "entries": compiled,
    }


def generate_registry(contract_def_path: Path, contracts_dir: Path, out_dir: Path, static_path: Path) -> None:
    contract_def = contract_def_path.read_text(encoding="utf-8")
    defs = parse_contract_def_h(contract_def)
    headers: Dict[str, int] = {Path(d.header).name: d.contract_index for d in defs}

    qpi_path = contracts_dir / "qpi.h"
    qpi = qpi_path.read_text(encoding="utf-8")

    static = json.loads(static_path.read_text(encoding="utf-8"))
    static_filenames = {sc["filename"] for sc in static.get("smart_contracts", []) if "filename" in sc}

    out_dir.mkdir(parents=True, exist_ok=True)
    excluded_dir = out_dir / "_excluded"
    excluded_dir.mkdir(parents=True, exist_ok=True)

    for header in contracts_dir.iterdir():
        if not header.is_file():
            continue
        if not header.name.endswith(".h"):
            continue
        if not re.match(r"^[A-Z].*\.h$", header.name):
            continue
        if header.name == "qpi.h":
            continue

        source = header.read_text(encoding="utf-8")
        source_with_qpi = f"{qpi}\n{source}"
        contract_name = header.stem
        contract_index = headers.get(header.name)
        qbi = compile_contract_header(source_with_qpi, contract_name, contract_index)

        out_name = f"{contract_name}.json"
        if header.name in static_filenames:
            out_path = out_dir / out_name
        else:
            out_path = excluded_dir / out_name

        out_path.write_text(json.dumps(qbi, indent=2), encoding="utf-8")


def ensure_core_repo(core_dir: Path, core_repo_url: str, core_revision: str) -> None:
    if not core_dir.exists():
        core_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", core_repo_url, str(core_dir)], check=True)

    status = subprocess.run(
        ["git", "-C", str(core_dir), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RuntimeError(f"Core repo has local changes in {core_dir}")

    subprocess.run(["git", "-C", str(core_dir), "fetch", "--all", "--tags", "--prune"], check=True)
    subprocess.run(["git", "-C", str(core_dir), "checkout", core_revision], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate QBI registry JSON for static repo.")
    parser.add_argument("--core-revision", required=True, help="qubic/core git SHA to check out")
    parser.add_argument("--core-repo", default="https://github.com/qubic/core", help="qubic/core repo URL")
    parser.add_argument("--core-dir", default="tools/core", help="Path to qubic/core checkout")
    parser.add_argument("--contracts-dir", default="tools/core/src/contracts", help="Path to contracts directory")
    parser.add_argument("--contract-def", default="tools/core/src/contract_core/contract_def.h", help="Path to contract_def.h")
    parser.add_argument("--out-dir", default="data/qbi/registry", help="Output directory for QBI JSON")
    parser.add_argument("--static-data", default="data/smart_contracts.json", help="Path to smart_contracts.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    core_dir = (repo_root / args.core_dir).resolve()
    ensure_core_repo(core_dir, args.core_repo, args.core_revision)

    generate_registry(
        contract_def_path=(repo_root / args.contract_def).resolve(),
        contracts_dir=(repo_root / args.contracts_dir).resolve(),
        out_dir=(repo_root / args.out_dir).resolve(),
        static_path=(repo_root / args.static_data).resolve(),
    )

    print(f"QBI registry generated in {args.out_dir}")


if __name__ == "__main__":
    main()
