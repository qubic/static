#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

API_VERSION_SUBDIR = "v1"

def write_json(path: Path, data: Any, pretty: bool = True) -> None:
    if pretty:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

def copy_data_to_dir(src_dir: Path, dst_dir: Path) -> List[Path]:
    """Recursively copy all files from src_dir to dst_dir."""
    copied: List[Path] = []
    for src in src_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied

def minify_each_json_in_dir(root_dir: Path) -> List[Path]:
    """Create minified .min.json versions of all JSON files."""
    created: List[Path] = []
    for p in root_dir.rglob("*.json"):
        if p.name.endswith(".min.json"):
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Skip minifying {p}: {e}")
            continue
        min_path = p.with_name(p.stem + ".min.json")
        write_json(min_path, obj, pretty=False)
        created.append(min_path)
    return created

def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"

def build_bundle_flat(root_dir: Path, bundle_name: str = "bundle.json") -> Tuple[Path, Path]:
    """Merge all JSON files into bundle.json and bundle.min.json."""
    merged: Dict[str, Any] = {}
    for p in sorted(root_dir.rglob("*.json")):
        if p.name.endswith(".min.json") or p.name == "version.json":
            continue
        rel = p.relative_to(root_dir).as_posix()
        if rel.startswith("qbi/"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
            else:
                merged[p.stem] = data
        except Exception as e:
            print(f"Skip bundling {p}: {e}")

    out_pretty = root_dir / bundle_name
    out_min = root_dir / (Path(bundle_name).stem + ".min.json")
    write_json(out_pretty, merged, pretty=True)
    write_json(out_min, merged, pretty=False)
    return out_pretty, out_min

def generate_version_file(root_dir: Path, version: str, environment: str) -> Path:
    """Generate version.json with file hashes and sizes for ALL files."""
    files_metadata: Dict[str, Dict[str, Any]] = {}

    # Collect all files (JSON, XML, etc.) except version.json and .min.json
    for p in sorted(root_dir.rglob("*")):
        if p.is_dir():
            continue
        if p.name == "version.json" or p.name.endswith(".min.json"):
            continue

        try:
            file_hash = calculate_file_hash(p)
            file_size = p.stat().st_size

            # Use relative filename from root_dir
            rel_name = p.relative_to(root_dir).as_posix()
            files_metadata[rel_name] = {
                "hash": file_hash,
                "size": file_size
            }
        except Exception as e:
            print(f"Warning: Could not process {p}: {e}")

    version_data = {
        "version": version,
        "environment": environment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files_metadata
    }

    version_path = root_dir / "version.json"
    write_json(version_path, version_data, pretty=True)
    return version_path

def build_product(product_name: str, src_dir: Path, dist_dir: Path, version: str, environment: str) -> None:
    """Build a single product."""
    print(f"\n{'='*60}")
    print(f"Building product: {product_name}")
    print(f"{'='*60}")

    # Copy all files
    copied = copy_data_to_dir(src_dir, dist_dir)
    print(f"Copied {len(copied)} files to {dist_dir}")

    # Minify JSON files
    mins = minify_each_json_in_dir(dist_dir)
    print(f"Created {len(mins)} minified JSONs")

    # Create bundle files
    bundle_path, bundle_min_path = build_bundle_flat(dist_dir, "bundle.json")
    print(f"Bundle: {bundle_path}")
    print(f"Bundle (min): {bundle_min_path}")

    # Generate version.json
    version_path = generate_version_file(dist_dir, version, environment)
    print(f"Version: {version_path}")

def get_available_products(base_dir: Path) -> Dict[str, Path]:
    """Get all available products (general + products/*)."""
    products = {}

    # General data (root data/ folder)
    data_dir = base_dir / "data"
    if data_dir.exists():
        products["general"] = data_dir

    # Product-specific folders
    products_dir = base_dir / "products"
    if products_dir.exists():
        for product_path in products_dir.iterdir():
            if product_path.is_dir():
                products[product_path.name] = product_path

    return products

def main():
    ap = argparse.ArgumentParser(
        description="Build distribution files for Qubic static API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build only general data (production)
  python3 scripts/build_dist.py --product general --version v1.2.3

  # Build only wallet-app (production)
  python3 scripts/build_dist.py --product wallet-app --version v1.2.3

  # Build all products (production)
  python3 scripts/build_dist.py --product all --version v1.2.3

  # Build for staging environment
  python3 scripts/build_dist.py --product all --version v1.2.3-rc.1 --environment staging

  # Build for dev environment
  python3 scripts/build_dist.py --product all --version v1.2.3-dev --environment dev
        """
    )
    ap.add_argument("--product", required=True, help="Product to build: 'general', 'wallet-app', 'all', or any product name")
    ap.add_argument("--dist-dir", default="dist", help="Path to dist directory (default: dist)")
    ap.add_argument("--environment", default="production", choices=["dev", "staging", "production"], help="Build environment")
    ap.add_argument("--version", required=True, help="Version string (e.g., v1.2.3 or v1.2.3-rc.1)")
    args = ap.parse_args()

    base_dir = Path.cwd()
    dist_root = Path(args.dist_dir).resolve()

    # Get available products
    available_products = get_available_products(base_dir)

    if not available_products:
        print("Error: No products found. Expected 'data/' folder or 'products/*' folders.")
        return

    print(f"Available products: {', '.join(available_products.keys())}")

    # Determine which products to build
    if args.product == "all":
        products_to_build = available_products
    elif args.product in available_products:
        products_to_build = {args.product: available_products[args.product]}
    else:
        print(f"Error: Product '{args.product}' not found.")
        print(f"Available products: {', '.join(available_products.keys())}")
        return

    # Build each product
    for product_name, src_dir in products_to_build.items():
        # All environments use the same structure: dist/v1/{product}/
        # FTP users are already scoped to their environment directories
        env_path = Path(API_VERSION_SUBDIR)

        # Determine output directory
        if product_name == "general":
            # General goes to dist/v1/general/data/
            product_dist_dir = dist_root / env_path / "general" / "data"
        else:
            # Products go to dist/v1/{product-name}/
            product_dist_dir = dist_root / env_path / product_name

        product_dist_dir.mkdir(parents=True, exist_ok=True)

        # Build the product
        build_product(product_name, src_dir, product_dist_dir, args.version, args.environment)

    # Copy index.html to dist root (for landing page)
    index_html = base_dir / "index.html"
    if index_html.exists():
        dist_index = dist_root / "index.html"
        shutil.copy2(index_html, dist_index)
        print(f"\nCopied index.html to {dist_index}")

    print(f"\n{'='*60}")
    print("Build completed successfully!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
