#!/usr/bin/env python3
"""
verify_outputs.py — Verification and reporting for disaster relief mesh generation.

Checks completeness, integrity, and spending across all geographies.

Usage:
  python verify_outputs.py
  python verify_outputs.py --geography germany
  python verify_outputs.py --spending-only
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
MANIFEST_DIR = PROJECT_DIR / "manifests"
OUTPUT_DIR = PROJECT_DIR / "outputs_relief"
STATE_DIR = PROJECT_DIR / "state"
SPENDING_FILE = STATE_DIR / "spending.json"
FAILED_FILE = STATE_DIR / "failed.json"


def load_manifests(geography: str | None = None) -> dict:
    """Load all manifests, keyed by geography."""
    manifests = {}
    for f in sorted(MANIFEST_DIR.glob("relief_manifest_*.json")):
        with open(f) as fh:
            data = json.load(fh)
        geo = data.get("geography", f.stem.split("_")[-1])
        if geography and geo != geography:
            continue
        manifests[geo] = data
    return manifests


def scan_outputs() -> dict:
    """Scan output directory for completed meshes."""
    results = {}
    if not OUTPUT_DIR.exists():
        return results

    for glb in OUTPUT_DIR.rglob("*.glb"):
        if glb.stat().st_size < 1024:
            continue
        # Path: outputs_relief/{geography}/{category}/{item_id}/{item_id}.glb
        parts = glb.relative_to(OUTPUT_DIR).parts
        if len(parts) >= 3:
            geo, cat, item_id = parts[0], parts[1], parts[2]
            if geo not in results:
                results[geo] = {}
            if cat not in results[geo]:
                results[geo][cat] = set()
            results[geo][cat].add(item_id)

    return results


def verify_completeness(manifests: dict, outputs: dict) -> dict:
    """Cross-reference manifests against outputs."""
    report = {}
    for geo, data in manifests.items():
        manifest_items = {m["id"] for m in data["meshes"]}
        manifest_cats = {}
        for m in data["meshes"]:
            cat = m.get("category", "unknown")
            manifest_cats.setdefault(cat, set()).add(m["id"])

        output_cats = outputs.get(geo, {})

        geo_report = {
            "expected": len(manifest_items),
            "complete": 0,
            "missing": 0,
            "categories": {},
        }

        all_found = set()
        for cat, expected_ids in manifest_cats.items():
            found_ids = output_cats.get(cat, set()) & expected_ids
            all_found.update(found_ids)
            geo_report["categories"][cat] = {
                "expected": len(expected_ids),
                "found": len(found_ids),
                "missing": len(expected_ids - found_ids),
            }

        geo_report["complete"] = len(all_found)
        geo_report["missing"] = len(manifest_items - all_found)
        report[geo] = geo_report

    return report


def check_integrity() -> dict:
    """Check file integrity of outputs."""
    issues = {
        "corrupt_glb": [],    # GLB files < 1KB
        "missing_png": 0,     # Items without reference image
        "missing_metadata": 0,  # Items without metadata.json
    }

    if not OUTPUT_DIR.exists():
        return issues

    for item_dir in OUTPUT_DIR.rglob("*"):
        if not item_dir.is_dir():
            continue
        glbs = list(item_dir.glob("*.glb"))
        if not glbs:
            continue

        for glb in glbs:
            if glb.stat().st_size < 1024:
                issues["corrupt_glb"].append(str(glb.relative_to(OUTPUT_DIR)))

        if not list(item_dir.glob("*.png")):
            issues["missing_png"] += 1

        if not (item_dir / "metadata.json").exists():
            issues["missing_metadata"] += 1

    return issues


def load_spending() -> dict:
    if SPENDING_FILE.exists():
        with open(SPENDING_FILE) as f:
            return json.load(f)
    return {"keys": {}}


def load_failed() -> list:
    if FAILED_FILE.exists():
        with open(FAILED_FILE) as f:
            return json.load(f).get("failures", [])
    return []


def print_report(completeness: dict, integrity: dict, spending: dict,
                 failed: list, spending_only: bool = False):
    """Print formatted verification report."""
    from datetime import datetime

    print("=" * 70)
    print("  DISASTER RELIEF MESH GENERATION — VERIFICATION REPORT")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    if not spending_only:
        # Completeness
        total_expected = 0
        total_complete = 0
        total_missing = 0

        print(f"\n{'Geography':<15} | {'Expected':>8} | {'Complete':>8} | {'Missing':>7} | {'% Done':>6}")
        print("-" * 62)

        for geo in sorted(completeness.keys()):
            r = completeness[geo]
            pct = (r["complete"] / r["expected"] * 100) if r["expected"] > 0 else 0
            total_expected += r["expected"]
            total_complete += r["complete"]
            total_missing += r["missing"]
            print(f"{geo:<15} | {r['expected']:>8,} | {r['complete']:>8,} | "
                  f"{r['missing']:>7,} | {pct:>5.1f}%")

        total_pct = (total_complete / total_expected * 100) if total_expected > 0 else 0
        print("-" * 62)
        print(f"{'TOTAL':<15} | {total_expected:>8,} | {total_complete:>8,} | "
              f"{total_missing:>7,} | {total_pct:>5.1f}%")

        # Per-category breakdown
        for geo in sorted(completeness.keys()):
            r = completeness[geo]
            cats = r["categories"]
            if any(c["missing"] > 0 for c in cats.values()):
                print(f"\n  {geo} category breakdown:")
                for cat in sorted(cats.keys()):
                    c = cats[cat]
                    pct = (c["found"] / c["expected"] * 100) if c["expected"] > 0 else 0
                    print(f"    {cat:<25} {c['found']:>5}/{c['expected']:<5} ({pct:.0f}%)")

        # Integrity
        print(f"\nINTEGRITY:")
        print(f"  Corrupt GLB files (<1KB): {len(integrity['corrupt_glb'])}")
        print(f"  Missing PNG references:   {integrity['missing_png']}")
        print(f"  Missing metadata.json:    {integrity['missing_metadata']}")
        if integrity["corrupt_glb"]:
            print(f"  Corrupt files:")
            for f in integrity["corrupt_glb"][:10]:
                print(f"    - {f}")

    # Spending
    keys = spending.get("keys", {})
    if keys:
        print(f"\nSPENDING:")
        print(f"{'Key':<25} | {'Provider':<10} | {'Budget':>8} | {'Spent':>8} | {'Requests':>8}")
        print("-" * 68)
        total_spent = 0
        total_requests = 0
        for name, data in sorted(keys.items()):
            spent = data.get("total_spent", 0)
            budget = data.get("max_budget", 0)
            reqs = data.get("request_count", 0)
            total_spent += spent
            total_requests += reqs
            budget_str = f"${budget:>7.0f}" if budget > 0 else "   N/A  "
            print(f"{name:<25} | {data.get('provider', '?'):<10} | "
                  f"{budget_str} | ${spent:>7.2f} | {reqs:>8}")
        print("-" * 68)
        print(f"{'TOTAL':<25} | {'':<10} | {'':>8} | ${total_spent:>7.2f} | {total_requests:>8}")

    # Failed items
    if failed:
        retryable = [f for f in failed if f.get("retry_count", 0) < f.get("max_retries", 3)]
        permanent = [f for f in failed if f.get("retry_count", 0) >= f.get("max_retries", 3)]
        print(f"\nFAILED ITEMS:")
        print(f"  Total failures:  {len(failed)}")
        print(f"  Retryable:       {len(retryable)}")
        print(f"  Permanent:       {len(permanent)}")
        if retryable:
            print(f"\n  Latest failures:")
            for f in retryable[-10:]:
                print(f"    {f['id']}: {f['error'][:60]}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Verify disaster relief mesh outputs")
    parser.add_argument("--geography", choices=["germany", "eu", "ukraine", "general"],
                        help="Only verify specific geography")
    parser.add_argument("--spending-only", action="store_true",
                        help="Only show spending report")
    args = parser.parse_args()

    spending = load_spending()
    failed = load_failed()

    if args.spending_only:
        print_report({}, {}, spending, failed, spending_only=True)
        return

    manifests = load_manifests(geography=args.geography)
    outputs = scan_outputs()
    completeness = verify_completeness(manifests, outputs)
    integrity = check_integrity()

    print_report(completeness, integrity, spending, failed)


if __name__ == "__main__":
    main()
