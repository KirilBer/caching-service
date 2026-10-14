from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caching_service.api import Settings, create_app
from caching_service.schemas import PayloadInput
from caching_service.storage import Payload, Store, Transformation


def test_example_and_file(tmp_path):
    transformer = Mock(side_effect=str.upper)
    with TestClient(create_app(Settings(data_dir=tmp_path), transformer)) as client:
        body = {
            "list_1": ["first string", "second string", "third string"],
            "list_2": ["other string", "another string", "last string"],
        }
        response = client.post("/payload", json=body)
        assert response.status_code == 200
        identifier = response.json()["id"]
        expected = {
            "output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, "
            "THIRD STRING, LAST STRING"
        }
        assert client.get(f"/payload/{identifier}").json() == expected
        path = tmp_path / "payloads" / f"{identifier}.json"
        assert path.is_file()
        path.unlink()
        assert client.get(f"/payload/{identifier}").json() == expected
        assert path.is_file()
        assert transformer.call_count == 6


def test_cache_reuse_across_payloads_and_restart(tmp_path):
    transformer = Mock(side_effect=str.upper)
    body = {"list_1": ["a", "a"], "list_2": ["b", "a"]}
    with TestClient(create_app(Settings(data_dir=tmp_path), transformer)) as client:
        identifier = client.post("/payload", json=body).json()["id"]
        assert client.post("/payload", json=body).json()["id"] == identifier
        assert transformer.call_count == 2
        other = client.post("/payload", json={"list_1": ["b"], "list_2": ["c"]})
        assert other.json()["id"] != identifier
        assert transformer.call_count == 3
    forbidden = Mock(side_effect=AssertionError("Must use persisted cache"))
    with TestClient(create_app(Settings(data_dir=tmp_path), forbidden)) as client:
        assert client.post("/payload", json=body).json()["id"] == identifier
        assert client.get(f"/payload/{identifier}").json() == {"output": "A, B, A, A"}
        assert client.post("/payload", json={"list_1": ["c"], "list_2": ["a"]}).status_code == 200
        forbidden.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"list_1": ["a"], "list_2": []},
        {"list_1": [1], "list_2": ["a"]},
        {"list_1": [None], "list_2": ["a"]},
        {"list_1": "a", "list_2": "b"},
        {"list_1": [], "list_2": [], "typo": []},
        {"list_1": ["x" * 10_001], "list_2": ["a"]},
        {"list_1": ["x"] * 1_001, "list_2": ["y"] * 1_001},
        {},
    ],
)
def test_invalid_inputs_do_not_transform(tmp_path, body):
    transformer = Mock(side_effect=str.upper)
    with TestClient(create_app(Settings(data_dir=tmp_path), transformer)) as client:
        assert client.post("/payload", json=body).status_code == 422
    transformer.assert_not_called()


def test_empty_unicode_and_missing(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        empty = client.post("/payload", json={"list_1": [], "list_2": []}).json()["id"]
        assert client.get(f"/payload/{empty}").json() == {"output": ""}
        identifier = client.post(
            "/payload", json={"list_1": ["straße", ""], "list_2": ["привіт", "a, b"]}
        ).json()["id"]
        assert client.get(f"/payload/{identifier}").json() == {"output": "STRASSE, ПРИВІТ, , A, B"}
        assert client.get("/payload/" + "0" * 64).status_code == 404
        assert client.get("/payload/invalid").status_code == 422


def test_concurrent_workers_transform_each_source_once(tmp_path):
    transformer = Mock(side_effect=str.upper)
    # Independent engines simulate workers without sharing an in-process lock.
    stores = [Store(tmp_path, transformer) for _ in range(4)]
    request = PayloadInput(list_1=["a", "b", "a"], list_2=["b", "c", "c"])
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(lambda index: stores[index % 4].create(request), range(16)))
        assert len(set(ids)) == 1
        assert transformer.call_count == 3
        with Session(stores[0].engine) as session:
            assert session.scalar(select(func.count()).select_from(Payload)) == 1
            assert session.scalar(select(func.count()).select_from(Transformation)) == 3
    finally:
        for store in stores:
            store.close()


def test_transform_failure_rolls_back(tmp_path):
    transformer = Mock(side_effect=["A", RuntimeError("upstream failed")])
    store = Store(tmp_path, transformer)
    request = PayloadInput(list_1=["a"], list_2=["b"])
    try:
        with pytest.raises(RuntimeError, match="upstream failed"):
            store.create(request)
        with Session(store.engine) as session:
            assert session.scalar(select(func.count()).select_from(Transformation)) == 0
            assert session.scalar(select(func.count()).select_from(Payload)) == 0
        store.transformer = str.upper
        assert store.read(store.create(request)).output == "A, B"
    finally:
        store.close()


def test_file_failure_is_recoverable(tmp_path, monkeypatch):
    transformer = Mock(side_effect=str.upper)
    store = Store(tmp_path, transformer)
    request = PayloadInput(list_1=["a"], list_2=["b"])
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, "materialize", Mock(side_effect=OSError("disk full")))
            with pytest.raises(OSError, match="disk full"):
                store.create(request)
        identifier = store.create(request)
        assert transformer.call_count == 2
        assert store.read(identifier).output == "A, B"
        assert (Path(tmp_path) / "payloads" / f"{identifier}.json").is_file()
    finally:
        store.close()


def test_identity_preserves_order_and_reuses_identical_output(tmp_path):
    store = Store(tmp_path)
    try:
        inputs = [
            PayloadInput(list_1=["a", "b"], list_2=["c", "d"]),
            PayloadInput(list_1=["b", "a"], list_2=["c", "d"]),
            PayloadInput(list_1=["a, b"], list_2=["c"]),
            PayloadInput(list_1=["a"], list_2=["b, c"]),
            PayloadInput(list_1=["A", "b"], list_2=["c", "d"]),
        ]
        ids = [store.create(body) for body in inputs]
        assert ids[0] != ids[1]
        assert ids[2] == ids[3]
        assert ids[0] == ids[4]
    finally:
        store.close()
