"""Regression cover for the ``output.ensure_ascii`` config flag.

Three JSON-writing paths in ``tools/code_indexer.py`` used to compute
``ensure_ascii = not output_config.get("ensure_ascii", False)`` — inverting the
flag, so both the shipped ``ensure_ascii: false`` and the ``False`` fallback
produced ``\\uXXXX``-escaped output. These tests pin the direction: the config
value reaches ``json.dump`` unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.code_indexer import CodeIndexer

_NON_ASCII = "工作流/代码索引.py"
_SHIPPED_CONFIG = ROOT / "tools" / "indexer_config.yaml"

_STATS_ROW = {
    "analyzed_files": 1,
    "total_relationships": 0,
    "total_lines_of_code": 10,
    "relationship_type_counts": {},
    "file_type_counts": {_NON_ASCII: 1},
}


def _indexer(tmp_path: Path, *, ensure_ascii: bool | None) -> CodeIndexer:
    """Build an indexer whose ``output.ensure_ascii`` is exactly ``ensure_ascii``.

    ``None`` leaves the key absent so the in-code ``False`` fallback applies.
    """

    indexer = CodeIndexer(output_dir=str(tmp_path / "indexes"))
    output = indexer.indexer_config.setdefault("output", {})
    if ensure_ascii is None:
        output.pop("ensure_ascii", None)
    else:
        output["ensure_ascii"] = ensure_ascii
    return indexer


def _write(indexer: CodeIndexer, method: str) -> str:
    if method == "statistics":
        return indexer.generate_statistics_report([_STATS_ROW])
    return indexer.generate_summary_report({_NON_ASCII: "out.json"})


def test_shipped_config_requests_literal_utf8():
    """``tools/indexer_config.yaml`` ships ``ensure_ascii: false``."""

    import yaml

    shipped = yaml.safe_load(_SHIPPED_CONFIG.read_text(encoding="utf-8"))
    assert shipped["output"]["ensure_ascii"] is False


def test_shipped_config_is_honored_end_to_end(tmp_path):
    indexer = CodeIndexer(
        output_dir=str(tmp_path / "indexes"),
        indexer_config_path=str(_SHIPPED_CONFIG),
    )
    assert indexer.indexer_config["output"]["ensure_ascii"] is False

    raw = Path(_write(indexer, "summary")).read_text(encoding="utf-8")
    assert _NON_ASCII in raw
    assert "\\u" not in raw


@pytest.mark.parametrize("method", ["statistics", "summary"])
def test_ensure_ascii_false_writes_literal_utf8(tmp_path, method):
    raw = Path(_write(_indexer(tmp_path, ensure_ascii=False), method)).read_text(
        encoding="utf-8"
    )
    assert _NON_ASCII in raw
    assert "\\u" not in raw


@pytest.mark.parametrize("method", ["statistics", "summary"])
def test_ensure_ascii_true_escapes(tmp_path, method):
    raw = Path(_write(_indexer(tmp_path, ensure_ascii=True), method)).read_text(
        encoding="utf-8"
    )
    assert _NON_ASCII not in raw
    assert "\\u5de5\\u4f5c\\u6d41" in raw  # 工作流
    # Still valid JSON that round-trips to the original text.
    assert _NON_ASCII in json.dumps(json.loads(raw), ensure_ascii=False)


@pytest.mark.parametrize("method", ["statistics", "summary"])
def test_absent_key_defaults_to_literal_utf8(tmp_path, method):
    """The in-code fallback is ``False`` — this is the default runtime path,
    since ``CodeIndexer`` does not load the shipped YAML unless told to."""

    raw = Path(_write(_indexer(tmp_path, ensure_ascii=None), method)).read_text(
        encoding="utf-8"
    )
    assert _NON_ASCII in raw
