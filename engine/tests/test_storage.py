"""Disk housekeeping: old conversions are removed automatically; a full disk gives a clear 507."""

import json
import os
import time

import pytest

from app.storage import BundleStore, DiskFull


def _make(store: BundleStore, bundle: dict, n: int) -> list[str]:
    ids = []
    for i in range(n):
        bid = store.create(bundle)
        t = time.time() - (n - i) * 10  # oldest first
        os.utime(store.bundle_dir(bid), (t, t))
        ids.append(bid)
    return ids


def test_only_recent_conversions_are_kept(tmp_path, bundle):
    store = BundleStore(tmp_path, keep=3, min_free_mb=0)
    ids = _make(store, bundle, 5)
    left = {d.name for d in store.bundles_oldest_first()}
    assert len(left) == 3 and ids[-1] in left and ids[0] not in left


def test_running_conversions_are_protected(tmp_path, bundle):
    store = BundleStore(tmp_path, keep=10, min_free_mb=0)
    ids = _make(store, bundle, 2)
    store.keep = 1
    newest = store.create(bundle, protect={ids[0]})  # ids[0] is still converting
    left = {d.name for d in store.bundles_oldest_first()}
    assert left == {ids[0], newest}


def test_low_disk_removes_old_conversions_then_refuses(tmp_path, bundle, monkeypatch):
    store = BundleStore(tmp_path, keep=10, min_free_mb=100)
    ids = _make(store, bundle, 3)
    free = {"bytes": 50 * 2**20}

    def fake_free():
        return free["bytes"] + 30 * 2**20 * (3 - len(store.bundles_oldest_first()))

    monkeypatch.setattr(store, "free_bytes", fake_free)
    store.ensure_space()  # frees by deleting the two oldest
    assert [d.name for d in store.bundles_oldest_first()] == [ids[2]]
    free["bytes"] = 0
    with pytest.raises(DiskFull) as err:
        store.create(bundle)
    assert "Free some space" in str(err.value)


def test_api_reports_disk_full_clearly(client, bundle, monkeypatch):
    from app import main

    monkeypatch.setattr(main.store, "min_free", 10**15)  # pretend the disk is full
    res = client.post("/v1/bundles", content=json.dumps(bundle))
    assert res.status_code == 507
    body = res.json()
    assert body["code"] == "DISK_FULL" and "disk is full" in body["detail"]
