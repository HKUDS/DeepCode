# SWE-bench harness

The P2 exit gate: measure whether the DeepCode agent can resolve a real
GitHub issue — produce a patch that flips the hidden `FAIL_TO_PASS` tests to
passing without breaking `PASS_TO_PASS`.

The harness separates the two jobs so DeepCode owns only what is DeepCode's:

| Stage | Who does it | Where |
| --- | --- | --- |
| **Predict** — check out repo @ `base_commit`, run `deepcode exec` on the issue, capture `git diff` as `model_patch` | DeepCode | `predict.py` |
| **Score** — apply the patch, run the tests, decide *resolved* | official `swebench` Docker harness (Verified) / local scorer (dev) | `evaluate.py` (local) |

We do **not** reimplement the official scorer — for the Verified set the
harness emits `predictions.jsonl` and hands off to
`swebench.harness.run_evaluation`.

## Local mode (Docker-free, runs anywhere)

A built-in benchmark of three self-contained instances — each a real bug
(off-by-one, case-sensitivity, missing empty-guard) plus an unrelated correct
function, mirroring SWE-bench's "fix the bug, don't break the neighbours"
shape. Runs the full loop end to end and prints a real resolved rate:

```bash
python -m eval.swebench.run --mode local --model gpt-5.4 --report report.json
```

Result on this machine (Poe `gpt-5.4`): **3/3 resolved (100%)** — every
instance's `FAIL_TO_PASS` flipped and `PASS_TO_PASS` stayed green. This
validates the whole prepare → agent → apply → test → score pipeline against a
live model; it is a smoke baseline, not the official Verified number.

## Official Verified 50 (needs Docker)

Generate predictions for the deterministic 50-instance subset (sorted by
`instance_id`, so the set is reproducible with no RNG seed):

```bash
# needs `datasets` + network to stream the dataset and clone repos
uv run --with-requirements requirements.txt --with datasets \
  python -m eval.swebench.run --mode predict --limit 50 --out predictions.jsonl
```

Then score with the official Docker harness (the command is printed at the
end of the predict run):

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path predictions.jsonl \
  --max_workers 4 --run_id deepcode-p2
```

Scoring requires a running Docker daemon and the `swebench` package; it is not
reproduced here. Record the resolved rate it reports as the P2 baseline and
re-run it before each release so the number never silently regresses.

## Layout

- `instance.py` — `Instance` model + the local benchmark (declarative specs → git repos)
- `dataset.py` — load Verified + deterministic subset selection + record→`Instance`
- `predict.py` — clone, drive `deepcode exec`, capture the patch (bytecode/cache excluded)
- `evaluate.py` — local Docker-free scorer (apply to a clean checkout, run the tests)
- `report.py` — result rows + aggregate resolved rate (JSON + summary line)
- `run.py` — CLI wiring both modes
