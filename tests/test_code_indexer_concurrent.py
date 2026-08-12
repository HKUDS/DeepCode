"""Regression cover for concurrent file analysis in ``tools/code_indexer.py``.

``_process_files_concurrently`` collected bare coroutine objects in
``tasks`` but the cleanup paths called ``task.done()`` /
``task.cancelled()`` / ``task.cancel()`` on them. Coroutines have none of
those methods, so the ``finally`` block raised ``AttributeError`` on every
run — including successful ones — and the concurrent path (enabled by
default in ``tools/indexer_config.yaml``) always failed. The entries are
now real ``asyncio.Task`` objects, and this test pins that the concurrent
path returns its gathered results.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.code_indexer import CodeIndexer


def test_concurrent_processing_returns_gathered_results(tmp_path, monkeypatch):
    indexer = CodeIndexer(output_dir=str(tmp_path / "indexes"))
    indexer.enable_concurrent_analysis = True
    indexer.request_delay = 0

    files = [tmp_path / f"mod_{i}.py" for i in range(3)]
    for file_path in files:
        file_path.write_text("x = 1\n", encoding="utf-8")

    async def fake_analyze(file_path, index, total):
        return f"summary-{file_path.name}", [f"rel-{index}"]

    monkeypatch.setattr(
        indexer, "_analyze_single_file_with_relationships", fake_analyze
    )

    summaries, relationships = asyncio.run(indexer._process_files_concurrently(files))

    assert sorted(summaries) == [f"summary-{f.name}" for f in files]
    assert sorted(relationships) == ["rel-1", "rel-2", "rel-3"]
