"""Atomic catalog writes survive Windows readers without losing the old record."""
import json
import os

import pytest

from virtual_microscopy import datasets


@pytest.mark.skipif(os.name != "nt", reason="Windows manifest sharing semantics")
def test_atomic_manifest_retries_transient_windows_reader(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    datasets.atomic_json(path, {"completed": 1})
    real_replace = os.replace
    attempts = []
    def occupied(source, destination):
        attempts.append(1)
        assert json.loads(path.read_text()) == {"completed": 1}
        if len(attempts) <= 2:
            error = PermissionError("Windows reader holds destination")
            error.winerror = 5
            raise error
        real_replace(source, destination)
    monkeypatch.setattr(datasets.os, "replace", occupied)
    datasets.atomic_json(path, {"completed": 2})
    assert len(attempts) == 3
    assert json.loads(path.read_text()) == {"completed": 2}
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_manifest_preserves_old_value_on_permanent_failure(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    datasets.atomic_json(path, {"completed": 1})
    def denied(*_):
        raise PermissionError("Permanent permission failure")
    monkeypatch.setattr(datasets.os, "replace", denied)
    with pytest.raises(PermissionError):
        datasets.atomic_json(path, {"completed": 2})
    assert json.loads(path.read_text()) == {"completed": 1}
    assert list(tmp_path.iterdir()) == [path]
