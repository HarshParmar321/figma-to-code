import copy
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sample-bundle.json"


@pytest.fixture
def bundle() -> dict:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.storage import BundleStore

    monkeypatch.setattr(main, "store", BundleStore(tmp_path))
    return TestClient(main.app)
