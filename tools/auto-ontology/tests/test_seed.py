# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
import seed

# The shape demo-data writes (data/src/demo_data/ontology.py): one database, which both schemas name.
MODEL = """\
data_layer:
  databases:
  - id: database:us-equities
    dialect: duckdb
    schemas:
    - id: schema:us-equities:main
      name: main
      database_name: us_equities
      tables:
      - id: table:us-equities:daily_prices
        name: daily_prices
        description: Daily bars.
    - id: schema:us-equities-prediction:prediction
      name: prediction
      database_name: us_equities
      tables: []
"""
CHANGED = MODEL.replace("Daily bars.", "Daily bars. A window return runs from the close before the window.")


class FakeBackend(BaseHTTPRequestHandler):
    """Auto Ontology's backend (/api/model/import) and its ingestion service (/ingest) in one."""

    status = 200
    reply: dict[str, Any] = {"success": True, "summary": {"terms": 9}}
    received: list[dict[str, Any]] = []
    log: list[str] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append({"path": self.path, "type": self.headers["Content-Type"], "body": body.decode()})
        type(self).log.append(f"POST {self.path.split('?')[0]}")
        status, reply = (202, {"status": "accepted"}) if self.path == "/ingest" else (self.status, self.reply)
        payload = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


class FakeCatalog:
    """Auto Ontology's store, in memory: the databases it holds and the seed record."""

    def __init__(self, databases: set[str] | None = None, recorded: dict[str, str] | None = None) -> None:
        self.databases = set(databases or ())
        self.records = dict(recorded or {})

    def recorded(self, database: str) -> str | None:
        return self.records.get(database)

    def has(self, database: str) -> bool:
        return database in self.databases

    def reset(self, database: str) -> None:
        FakeBackend.log.append(f"reset {database}")
        self.databases.discard(database)

    def record(self, database: str, digest: str) -> None:
        FakeBackend.log.append(f"record {database}")
        self.records[database] = digest


@pytest.fixture
def backend() -> Iterator[str]:
    FakeBackend.received = []
    FakeBackend.log = []
    server = HTTPServer(("127.0.0.1", 0), FakeBackend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str) -> Path:
    path = tmp_path / "model.yaml"
    path.write_text(MODEL)
    monkeypatch.setenv("AUTO_ONTOLOGY_API_URL", backend)
    monkeypatch.setenv("AUTO_ONTOLOGY_MODEL", str(path))
    monkeypatch.setenv("INGESTION_SERVICE_URL", backend)
    return path


def seeded(recorded: str | None = None, *others: str) -> FakeCatalog:
    """A catalog that holds the model's database, seeded from the model with hash `recorded`."""
    return FakeCatalog({"us_equities", *others}, {"us_equities": recorded} if recorded else {})


def digest(model: str) -> str:
    return seed.model_digest(model.encode())


def test_posts_the_model_to_the_import_route(backend: str) -> None:
    summary = seed.import_model(backend + "/", MODEL.encode(), timeout=5)

    assert summary == {"terms": 9}
    assert FakeBackend.received == [
        {"path": "/api/model/import?replace=true&embed=true", "type": "application/x-yaml", "body": MODEL}
    ]


def test_a_first_seed_imports_and_records_the_model_hash(model: Path) -> None:
    catalog = FakeCatalog()

    assert seed.main(catalog) == 0

    assert FakeBackend.log == ["POST /api/model/import", "record us_equities"]
    assert catalog.records == {"us_equities": digest(MODEL)}


def test_an_unchanged_model_is_imported_again_without_a_reset(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catalog = seeded(digest(MODEL))

    assert seed.main(catalog) == 0
    assert seed.main(catalog) == 0

    assert FakeBackend.log == ["POST /api/model/import", "record us_equities"] * 2  # no reset, no ingest request
    assert "reset" not in capsys.readouterr().out


def test_a_changed_model_resets_only_its_database_before_the_import(
    model: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = seeded(digest(MODEL), "another_database")
    model.write_text(CHANGED)

    assert seed.main(catalog) == 0

    assert FakeBackend.log == ["reset us_equities", "POST /api/model/import", "record us_equities", "POST /ingest"]
    assert FakeBackend.received[0]["body"] == CHANGED
    assert FakeBackend.received[1]["body"] == json.dumps({"database": "us_equities"})
    assert catalog.databases == {"another_database"}  # the fake import does not add ours back
    assert catalog.records == {"us_equities": digest(CHANGED)}
    out = capsys.readouterr().out
    assert "reset the catalog of us_equities" in out
    assert "ingestion us_equities: requested" in out


def test_a_catalog_seeded_before_hashes_were_recorded_is_reset(model: Path) -> None:
    """A deployment seeded by the earlier seed has a catalog but no record: its first run makes the catalog current."""
    assert seed.main(seeded()) == 0

    assert FakeBackend.log[:3] == ["reset us_equities", "POST /api/model/import", "record us_equities"]


def test_a_failed_import_records_nothing_so_the_next_run_imports_again(
    model: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = seeded("an earlier model's hash")
    monkeypatch.setattr(FakeBackend, "status", 422)
    monkeypatch.setattr(FakeBackend, "reply", {"detail": "Unknown database ids"})

    assert seed.main(catalog) == 1
    assert "HTTP 422" in capsys.readouterr().err
    assert catalog.records == {"us_equities": "an earlier model's hash"}

    monkeypatch.setattr(FakeBackend, "status", 200)
    monkeypatch.setattr(FakeBackend, "reply", {"success": True, "summary": {}})
    assert seed.main(catalog) == 0
    assert FakeBackend.log == [
        "reset us_equities",
        "POST /api/model/import",  # rejected
        "POST /api/model/import",  # the database is gone already: nothing to reset
        "record us_equities",
    ]


def test_an_ingestion_service_that_is_not_running_does_not_fail_the_seed(
    model: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INGESTION_SERVICE_URL", "http://127.0.0.1:9")  # the discard port: nothing listens

    assert seed.main(seeded()) == 0
    assert "ingestion us_equities: not running" in capsys.readouterr().out


def test_an_import_without_success_fails(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeBackend, "reply", {"success": False})

    with pytest.raises(RuntimeError, match="did not report success"):
        seed.import_model(backend, MODEL.encode(), timeout=5)


def test_a_missing_model_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_ONTOLOGY_MODEL", str(tmp_path / "missing.yaml"))

    assert seed.main(FakeCatalog()) == 1


def test_without_the_store_the_seed_fails_in_one_line(model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Outside the backend image there is no auto_ontology package: the seed says so and imports nothing."""
    assert seed.main() == 1

    assert "ModuleNotFoundError" in capsys.readouterr().err
    assert FakeBackend.log == []


def test_database_names_are_the_ones_the_import_gives_the_catalog() -> None:
    two = b"""\
data_layer:
  databases:
  - id: database:a
    schemas:
    - {id: schema:a, name: main}
  - {id: database:b, schemas: []}
"""
    assert seed.database_names(MODEL.encode()) == ["us_equities"]  # once, though both schemas name it
    assert seed.database_names(two) == ["main", "database:b"]  # else the first schema's name, else the id


@pytest.mark.parametrize(
    ("model", "error"),
    [
        (b"data_layer:\n  databases: []\n", "names no database"),
        (b"- a list\n", "not a YAML mapping"),
        (b"data_layer: [unclosed\n", "not YAML"),
    ],
)
def test_a_model_without_a_database_is_rejected(model: bytes, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        seed.database_names(model)
