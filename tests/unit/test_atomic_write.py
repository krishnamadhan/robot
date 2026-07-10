"""AB-012 / KI-020 — atomic persistence + corrupt-file recovery."""

import json

import pytest

from utils.atomic_write import atomic_write_json, atomic_write_text


def test_atomic_write_text_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_text(p, '{"a": 1}')
    assert p.read_text() == '{"a": 1}'


def test_atomic_write_json_roundtrip(tmp_path):
    p = tmp_path / "nested" / "state.json"  # parent dir auto-created
    atomic_write_json(p, {"tv_sync": True}, indent=2)
    assert json.loads(p.read_text()) == {"tv_sync": True}


def test_atomic_write_replaces_not_appends(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 2}


def test_no_tmp_litter_on_success(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"v": 1})
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_tmp_cleaned_on_failure(tmp_path):
    p = tmp_path / "state.json"

    class Boom:
        def __str__(self):  # str() inside json.dumps default path never called;
            raise RuntimeError("boom")  # force failure via non-serializable obj

    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": object()})
    assert list(tmp_path.iterdir()) == []  # no stale .tmp, no partial target


def test_exploration_recovers_from_truncated_json(tmp_path, monkeypatch):
    import behavior.exploration as ex

    snap = tmp_path / "exploration_snapshots.json"
    snap.write_text('{"snapshots": [{"room_id": "liv')  # simulated power-cut torso
    monkeypatch.setattr(ex, "_SNAPSHOT_PATH", snap)
    mem = ex.ExplorationMemory()
    mem.load()  # must not raise
    assert mem._loaded is True
    assert len(mem._snapshots) == 0


def test_spatial_recovers_from_truncated_json(tmp_path, monkeypatch):
    import core.memory.spatial as sp

    path = tmp_path / "spatial.json"
    path.write_text('{"rooms": {"living": {"room_id": "liv')  # truncated
    monkeypatch.setattr(sp, "SPATIAL_PATH", path)
    mem = sp.SpatialMemory()  # _load runs in ctor on most builds; call defensively
    if hasattr(mem, "_load"):
        mem._load()  # must not raise
    assert mem.stats() is not None
