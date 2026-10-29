"""Project hook declaration trust — digest approval store (ZCode rule).

Contract under test:

- trusting a project once must not silently trust it forever: the digest
  covers the canonical DECLARATION content (event/matcher/command/timeout/
  priority/type), so any real change produces a new digest and the old
  approval no longer matches;
- cosmetic churn (key reordering, reindenting, non-declaration keys) does
  NOT invalidate an approval;
- a file appearing or disappearing changes the digest (stable markers for
  absent files);
- the store is per (workspace, digest), persists across instances, and
  treats a corrupt store file as empty instead of crashing;
- the gate is opt-in at the engine seam: with no store configured project
  discovery is untouched (verified structurally — the engine takes
  trust_store=None as its default).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.hooks.trust import HookTrustStore, declaration_digest

_DECLARATION_V1 = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "python lint.py", "timeout": 30}
                ],
            }
        ]
    }
}


def _write(ws_hooks: Path, payload: dict) -> Path:
    ws_hooks.parent.mkdir(parents=True, exist_ok=True)
    ws_hooks.write_text(json.dumps(payload), encoding="utf-8")
    return ws_hooks


def test_digest_is_stable_across_cosmetic_churn(tmp_path: Path) -> None:
    path = _write(tmp_path / "hooks.json", _DECLARATION_V1)
    before = declaration_digest(str(tmp_path), paths=(path,))

    # Same semantics: key order shuffled, extra whitespace, non-declaration
    # key added inside the handler, and an unrelated top-level key.
    churned = {
        "version": 2,
        "hooks": {
            "PostToolUse": [
                {
                    "hooks": [
                        {
                            "timeout": 30,
                            "command": "python lint.py",
                            "description": "cosmetic, not hashed",
                            "type": "command",
                        }
                    ],
                    "matcher": "Bash",
                }
            ]
        },
    }
    path.write_text(json.dumps(churned, indent=4), encoding="utf-8")
    after = declaration_digest(str(tmp_path), paths=(path,))
    assert after == before


def test_any_semantic_change_produces_a_new_digest(tmp_path: Path) -> None:
    path = _write(tmp_path / "hooks.json", _DECLARATION_V1)
    before = declaration_digest(str(tmp_path), paths=(path,))

    for mutation in (
        {"command": "python lint.py && rm -rf /"},  # command swapped
        {"timeout": 5},  # timeout changed
    ):
        payload = json.loads(json.dumps(_DECLARATION_V1))
        payload["hooks"]["PostToolUse"][0]["hooks"][0].update(mutation)
        _write(tmp_path / "hooks.json", payload)
        assert declaration_digest(str(tmp_path), paths=(path,)) != before

    # A matcher change is semantic too.
    payload = json.loads(json.dumps(_DECLARATION_V1))
    payload["hooks"]["PostToolUse"][0]["matcher"] = "Edit"
    _write(tmp_path / "hooks.json", payload)
    assert declaration_digest(str(tmp_path), paths=(path,)) != before


def test_a_file_appearing_or_disappearing_changes_the_digest(tmp_path: Path) -> None:
    first = _write(tmp_path / "a" / "hooks.json", _DECLARATION_V1)
    second = tmp_path / "b" / "hooks.json"  # absent

    with_neither = declaration_digest(str(tmp_path), paths=(first, second))
    _write(second, _DECLARATION_V1)
    with_both = declaration_digest(str(tmp_path), paths=(first, second))
    assert with_neither != with_both

    # Absent files contribute a stable marker: two runs over the same tree
    # agree, so the digest does not flap across restarts.
    assert declaration_digest(str(tmp_path), paths=(first, second)) == with_both


def test_declared_but_unreadable_content_differs_from_real_content(
    tmp_path: Path,
) -> None:
    real = _write(tmp_path / "real.json", _DECLARATION_V1)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    missing = tmp_path / "missing.json"

    real_digest = declaration_digest(str(tmp_path), paths=(real,))
    broken_digest = declaration_digest(str(tmp_path), paths=(broken,))
    missing_digest = declaration_digest(str(tmp_path), paths=(missing,))
    assert broken_digest != real_digest
    assert missing_digest != real_digest
    assert missing_digest == declaration_digest(str(tmp_path), paths=(missing,))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_approval_is_per_digest_and_persists_across_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hook_trust.json"
    store = HookTrustStore(path)
    workspace = str(tmp_path / "ws")
    digest = "d" * 64

    assert not store.is_approved(workspace, digest)
    store.approve(workspace, digest)
    store.approve(workspace, digest)  # idempotent
    assert store.is_approved(workspace, digest)

    # A fresh instance over the same file sees the approval (atomic write).
    assert HookTrustStore(path).is_approved(workspace, digest)

    # Another workspace and another digest are independent.
    assert not store.is_approved(str(tmp_path / "other-ws"), digest)
    assert not store.is_approved(workspace, "e" * 64)


def test_changed_declaration_reapproval_cycle(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    hooks = _write(ws / "hooks.json", _DECLARATION_V1)
    store = HookTrustStore(tmp_path / "hook_trust.json")

    old_digest = declaration_digest(str(ws), paths=(hooks,))
    store.approve(str(ws), old_digest)

    # Declaration changes -> new digest -> old approval no longer matches,
    # so project hooks are skipped until re-approved.
    payload = json.loads(json.dumps(_DECLARATION_V1))
    payload["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "python lint2.py"
    _write(ws / "hooks.json", payload)
    new_digest = declaration_digest(str(ws), paths=(hooks,))
    assert new_digest != old_digest
    assert not store.is_approved(str(ws), new_digest)

    store.approve(str(ws), new_digest)
    assert store.is_approved(str(ws), new_digest)
    # The old approval row remains harmless history; the gate checks the
    # CURRENT declaration's digest, which is the new one.
    assert store.is_approved(str(ws), old_digest)


def test_revoke_single_and_revoke_all(tmp_path: Path) -> None:
    store = HookTrustStore(tmp_path / "hook_trust.json")
    workspace = str(tmp_path / "ws")
    store.approve(workspace, "a" * 64)
    store.approve(workspace, "b" * 64)

    store.revoke(workspace, "a" * 64)
    assert not store.is_approved(workspace, "a" * 64)
    assert store.is_approved(workspace, "b" * 64)

    store.revoke(workspace)
    assert not store.is_approved(workspace, "b" * 64)
    # Revoking an unknown workspace/digest is a no-op, not an error.
    store.revoke(workspace)
    store.revoke(workspace, "c" * 64)


def test_corrupt_store_file_is_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "hook_trust.json"
    path.write_text("{corrupted", encoding="utf-8")
    store = HookTrustStore(path)
    assert not store.is_approved(str(tmp_path / "ws"), "d" * 64)

    # Approving over a corrupt file repairs the store.
    store.approve(str(tmp_path / "ws"), "d" * 64)
    assert HookTrustStore(path).is_approved(str(tmp_path / "ws"), "d" * 64)

    # A version bump invalidates old approvals instead of misreading them.
    path.write_text(
        json.dumps({"version": 99, "workspaces": {"ws": ["d" * 64]}}), encoding="utf-8"
    )
    assert not store.is_approved(str(tmp_path / "ws"), "d" * 64)


def test_engine_gate_is_opt_in_via_its_default_parameter() -> None:
    """Structural check: the discovery seam defaults trust_store to None (off)."""
    import inspect

    from core.harness.hooks.engine import HooksEngine

    parameter = inspect.signature(HooksEngine.discover).parameters.get("trust_store")
    assert parameter is not None
    assert parameter.default is None


def test_discover_hooks_gate_skips_project_sources_until_approved(
    tmp_path: Path,
) -> None:
    """End-to-end gate: unapproved -> project hooks skipped + warning."""
    from core.harness.hooks.discovery import discover_hooks

    ws = tmp_path / "ws"
    home = tmp_path / "home"  # empty user tree keeps discovery deterministic
    home.mkdir()
    _write(ws / ".deepcode" / "hooks.json", _DECLARATION_V1)
    store = HookTrustStore(tmp_path / "hook_trust.json")

    # No store: project hooks discovered as usual (feature dormant).
    baseline = discover_hooks(str(ws), home=str(home))
    assert any(h.source == "project" for h in baseline.handlers)

    # Store present but unapproved: project sources skipped + warning.
    gated = discover_hooks(str(ws), home=str(home), trust_store=store)
    assert not any(h.source == "project" for h in gated.handlers)
    assert any("not approved" in warning for warning in gated.warnings)

    # Approve the CURRENT digest: project hooks are discovered again.
    store.approve(str(ws), declaration_digest(str(ws), home=str(home)))
    approved = discover_hooks(str(ws), home=str(home), trust_store=store)
    assert any(h.source == "project" for h in approved.handlers)
    assert not any("not approved" in warning for warning in approved.warnings)
