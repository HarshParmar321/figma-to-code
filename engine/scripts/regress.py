"""Regression check: convert every saved design again and compare with the last good scores.

    uv run python scripts/regress.py              # check (exit code 1 if any design got worse)
    uv run python scripts/regress.py --update     # accept the current scores as the new baseline
    uv run python scripts/regress.py b_123 b_456  # only these bundles

Every design you convert in the plugin is saved under data/bundles/<id>. This re-runs the full
conversion (with the browser check) on each of them, so a fix made for one design cannot quietly
break another. Scores are kept in data/regression.json (per machine, not in git).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from app.config import settings  # noqa: E402
from app.pipeline.convert import convert  # noqa: E402

BUNDLES = ENGINE / "data" / "bundles"
BASELINE = ENGINE / "data" / "regression.json"
TOLERANCE = 0.5  # score points a design may move without counting as worse (rendering noise)


def scores(res: dict) -> dict:
    f = res.get("fidelity") or {}
    m = res.get("mobile") or {}
    return {
        "desktop": f.get("score"),
        "phone": m.get("score"),
        "inPlace": f"{(f.get('counts') or {}).get('geometryPass', 0)}/{(f.get('counts') or {}).get('elements', 0)}",
        "phoneInPlace": f"{m.get('elementsInPlace', 0)}/{m.get('elements', 0)}" if m else None,
    }


def label(bundle: dict) -> str:
    frames = bundle.get("frames") or []
    return ", ".join(f"{f.get('name')} ({f.get('role')})" for f in frames)[:70]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundles", nargs="*", help="bundle ids (default: all in data/bundles)")
    ap.add_argument("--update", action="store_true", help="save the current scores as the baseline")
    ap.add_argument("--dir", type=Path, default=BUNDLES, help="folder of saved bundles")
    ap.add_argument("--fonts", type=Path, default=settings.font_dir, help="self-hosted fonts folder")
    args = ap.parse_args()

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    dirs = [args.dir / b for b in args.bundles] if args.bundles else sorted(args.dir.iterdir())
    dirs = [d for d in dirs if (d / "bundle.json").exists() and (d / "assets").is_dir()]
    if not dirs:
        print(f"No saved designs in {args.dir}. Convert some in the plugin first.")
        return 1

    worse, results = [], {}
    for d in dirs:
        bundle = json.loads((d / "bundle.json").read_text())
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        hashes = {k: v.get("sha256", "") for k, v in (meta.get("assets") or {}).items()}
        started = time.time()
        try:
            res = convert(bundle, d, args.fonts, os.environ.get("ENGINE_CHROMIUM_PATH"), asset_hashes=hashes)
        except Exception as exc:  # a crash is the worst regression
            print(f"✗ {d.name}  CRASHED: {exc!r}")
            worse.append(d.name)
            continue
        now = scores(res)
        now["seconds"] = round(time.time() - started, 1)
        now["design"] = label(bundle)
        results[d.name] = now
        before = baseline.get(d.name) or {}
        drops = [
            f"{k} {before[k]} → {now[k]}"
            for k in ("desktop", "phone")
            if isinstance(before.get(k), (int, float)) and (now.get(k) or 0) < before[k] - TOLERANCE
        ]
        mark = "✗" if drops else "✓"
        if drops:
            worse.append(d.name)
        phone = f"  phone {now['phone']} ({now['phoneInPlace']})" if now["phone"] is not None else ""
        print(f"{mark} {d.name}  desktop {now['desktop']} ({now['inPlace']}){phone}  {now['seconds']}s  "
              f"{now['design']}" + (f"   WORSE: {'; '.join(drops)}" if drops else ""), flush=True)  # fmt: skip

    if args.update:
        baseline.update(results)
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(baseline, indent=2))
        print(f"Baseline saved ({len(results)} designs).")
    if worse:
        print(f"\n{len(worse)} design(s) got worse: {', '.join(worse)}")
        return 1
    print(f"\nAll {len(results)} designs as good as or better than before.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
