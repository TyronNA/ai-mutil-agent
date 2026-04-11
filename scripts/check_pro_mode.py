#!/usr/bin/env python3
"""Preflight check for Gemini Pro availability and routing.

Usage:
  python scripts/check_pro_mode.py
    python scripts/check_pro_mode.py --location us-central1 --probe-pro3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRO_CANDIDATES = [
    "gemini-2.5-pro",
    "gemini-3-pro-preview",
    "gemini-3-pro",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check if Pro model is actually available and routed.")
    parser.add_argument(
        "--location",
        default="",
        help="Override GCP_LOCATION for this probe (example: us-central1)",
    )
    parser.add_argument(
        "--pro-model",
        default="",
        help="Override PRO_MODEL for this probe",
    )
    parser.add_argument(
        "--probe-pro3",
        action="store_true",
        help="Probe candidate Pro model IDs in order",
    )
    args = parser.parse_args()

    load_dotenv(dotenv_path=ENV_FILE)

    if args.location:
        os.environ["GCP_LOCATION"] = args.location.strip()
    if args.pro_model:
        os.environ["PRO_MODEL"] = args.pro_model.strip()

    probe_models = [os.environ.get("PRO_MODEL", "")]
    if args.probe_pro3:
        probe_models = PRO_CANDIDATES

    # Import after dotenv/env overrides so llm module sees effective settings on init.
    from src import llm

    def _print_header(model_name: str) -> None:
        print(f"MODEL={os.environ.get('MODEL', llm.DEFAULT_MODEL)}")
        print(f"PRO_MODEL={model_name}")
        print(f"GCP_LOCATION={os.environ.get('GCP_LOCATION', 'us-central1')}")
        print(f"resolved_pro_model={llm.get_effective_model_name(pro=True)}")

    def _probe(model_name: str) -> tuple[bool, str, bool]:
        os.environ["PRO_MODEL"] = model_name
        _print_header(model_name)
        if "pro" not in llm.get_effective_model_name(pro=True).lower():
            return False, "Pro request is not resolving to a Pro model name.", False
        try:
            resp = llm.call(
                system="Reply with exactly: OK",
                user="ping",
                temperature=0.0,
                thinking_budget=0,
                pro=True,
            )
            return True, f"PROBE_OK: {resp[:80].strip()}", True
        except Exception as exc:
            return False, f"PROBE_FAIL: {exc}", False

    for model_name in probe_models:
        model_name = (model_name or llm.DEFAULT_PRO_MODEL).strip()
        ok, detail, real_pro = _probe(model_name)
        print(detail)
        if ok:
            if real_pro:
                print(f"RESULT: Pro route is working with {model_name}.")
            return 0

    print("RESULT: Pro is unavailable.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
