import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import broken_cluster  # noqa: E402

from kubesift.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(llm_provider="none", data_dir=str(tmp_path / "data"))


@pytest.fixture
def snap():
    return broken_cluster()


@pytest.fixture
def snapshot_file(tmp_path, snap):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap.to_dict(), default=str), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _wall_clock(monkeypatch):
    """`--from` makes the CLI judge a snapshot as of its collection time (a process-wide setting); reset it."""
    import kubesift.util

    monkeypatch.setattr(kubesift.util, "_as_of", None)
