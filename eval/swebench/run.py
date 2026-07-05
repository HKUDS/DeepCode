"""SWE-bench harness CLI.

Two modes:

* ``--mode local`` (default) — run the built-in Docker-free benchmark end to
  end (prepare → agent → apply → test → score) and print a real resolved
  rate. Use this to sanity-check the whole loop with a live model.

* ``--mode predict`` — the official Verified path. Load the deterministic
  50-subset, generate a ``model_patch`` per instance with ``deepcode exec``,
  and write ``predictions.jsonl`` in the SWE-bench schema. Scoring is then run
  with the official Docker harness (command printed at the end) — we do not
  reimplement it.

Examples::

    python -m eval.swebench.run --mode local --model gpt-5.4
    python -m eval.swebench.run --mode predict --limit 50 --out preds.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.swebench.evaluate import evaluate_local
from eval.swebench.instance import load_local_instances
from eval.swebench.predict import generate_prediction, prepare_workspace
from eval.swebench.report import Report

_DATASET = "princeton-nlp/SWE-bench_Verified"


def _run_local(model: str, scratch: Path) -> Report:
    instances = load_local_instances(scratch / "repos")
    report = Report(model=model, mode="local")
    for inst in instances:
        ws = scratch / "work" / inst.instance_id
        prepare_workspace(inst, ws)
        prediction = generate_prediction(inst, ws, model=model)
        row = evaluate_local(inst, prediction, scratch_root=scratch / "score")
        report.rows.append(row)
        mark = "RESOLVED" if row.resolved else "unresolved"
        print(f"  [{mark}] {inst.instance_id} — {row.detail}", flush=True)
    return report


def _run_predict(model: str, limit: int, out: Path, scratch: Path) -> int:
    """Generate official predictions.jsonl; return the count written."""
    from eval.swebench.dataset import load_verified, select_subset, to_instance

    records = select_subset(load_verified(), limit)
    written = 0
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            inst = to_instance(record)
            ws = scratch / "work" / inst.instance_id
            try:
                prepare_workspace(inst, ws)
                prediction = generate_prediction(inst, ws, model=model)
            except Exception as exc:  # keep going; a failed clone is one miss
                print(f"  [skip] {inst.instance_id}: {exc}", flush=True)
                prediction = {
                    "instance_id": inst.instance_id,
                    "model_name_or_path": model or "deepcode",
                    "model_patch": "",
                }
            prediction.pop("_completed", None)
            fh.write(json.dumps(prediction, ensure_ascii=False) + "\n")
            written += 1
            print(f"  [pred] {inst.instance_id}", flush=True)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swebench-harness")
    parser.add_argument("--mode", choices=("local", "predict"), default="local")
    parser.add_argument(
        "--model", default="", help="Model id (blank = config default)."
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Instances (predict mode)."
    )
    parser.add_argument(
        "--out", default="predictions.jsonl", help="predict-mode output."
    )
    parser.add_argument(
        "--report", default="", help="Write the local report JSON here."
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="swebench_") as tmp:
        scratch = Path(tmp)
        if args.mode == "local":
            report = _run_local(args.model, scratch)
            print("\n" + report.summary_line())
            if args.report:
                Path(args.report).write_text(report.to_json(), encoding="utf-8")
                print(f"report → {args.report}")
            # Non-zero exit if nothing resolved, so CI can gate on it.
            return 0 if report.resolved > 0 else 1

        out = Path(args.out).resolve()
        count = _run_predict(args.model, args.limit, out, scratch)

    print(f"\nWrote {count} predictions → {out}")
    print("Score with the official Docker harness (not reimplemented here):")
    print(
        f"  python -m swebench.harness.run_evaluation \\\n"
        f"    --dataset_name {_DATASET} \\\n"
        f"    --predictions_path {out} \\\n"
        f"    --max_workers 4 --run_id deepcode-p2"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
