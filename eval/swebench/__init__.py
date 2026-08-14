"""SWE-bench evaluation harness for DeepCode (P2 exit gate).

DEEPCODE_V2_MASTER_PLAN.md §7 P2 exits on "establish a SWE-bench Verified
50-subset baseline". SWE-bench measures whether an agent can resolve a real
GitHub issue: given a repo at a base commit plus the issue text, the agent
must produce a patch that makes the hidden FAIL_TO_PASS tests pass without
breaking the PASS_TO_PASS tests.

This harness has two cleanly separated stages, so DeepCode owns only the part
that is DeepCode's to own and reuses the rest:

1. **Prediction** (:mod:`eval.swebench.predict`) — *this* is the agent's job.
   Check out the repo at ``base_commit``, run ``deepcode exec`` with the
   problem statement, and capture ``git diff`` as the ``model_patch``, emitted
   in the exact SWE-bench predictions schema.
2. **Scoring** — for the official Verified set this is delegated verbatim to
   the ``swebench`` package's Docker harness (we do NOT reimplement the
   scorer — convergence-matrix reuse of a load-bearing wall). For quick,
   Docker-free iteration we ship a local scorer
   (:mod:`eval.swebench.evaluate`) over self-contained instances.

:mod:`eval.swebench.run` is the CLI tying them together.
"""

from eval.swebench.instance import Instance, load_local_instances
from eval.swebench.report import Report, ResultRow

__all__ = ["Instance", "load_local_instances", "Report", "ResultRow"]
