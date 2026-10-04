# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from conftest import CATALOG_FIXTURES
from conftest import FIXTURES
from conftest import FakeEmbedder
from conftest import FakeParse
from conftest import write_awkward_xlsx
from jsonschema import Draft202012Validator

from demo_ingest.app import create_app
from demo_ingest.catalog import Catalog
from demo_ingest.index import KnowledgeIndex
from demo_ingest.settings import Settings
from demo_ingest.store import JobStore

pytestmark = pytest.mark.anyio
TABLES = CATALOG_FIXTURES / "tables"
SOURCE_SCHEMA = Draft202012Validator(
    json.loads((CATALOG_FIXTURES.parents[1] / "catalog" / "source-manifest.schema.json").read_text())
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def running(
    settings: Settings, embedder: FakeEmbedder, tokenizer: Any, parse_probe: Any = None
) -> AsyncIterator[httpx.AsyncClient]:
    index = KnowledgeIndex(settings.milvus_uri, settings.collection_alias)
    app = create_app(settings, embedder=embedder, index=index, tokenizer=tokenizer, parse_probe=parse_probe)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://ingest", timeout=60) as client:
                yield client
    finally:
        index.close()


@pytest.fixture
async def client(
    parse_settings: Settings, fake_parse: FakeParse, embedder, tokenizer
) -> AsyncIterator[httpx.AsyncClient]:
    fake_parse.label = "policy-pdf"
    async with running(parse_settings, embedder, tokenizer) as client:
        yield client


def upload(*files: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, data, "application/octet-stream")) for name, data in files]


async def wait(client: httpx.AsyncClient, job_id: str, timeout: float = 60) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await client.get(f"/v1/documents/{job_id}/status")
        assert response.status_code == 200
        status = response.json()
        if status["status"] in ("completed", "failed"):
            return status
        assert asyncio.get_running_loop().time() < deadline, status
        await asyncio.sleep(0.05)


def details(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["file_name"]: f for f in status["file_details"]}


async def test_health(client: httpx.AsyncClient):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_upload_a_table_and_a_pdf_then_watch_them_ingest(
    client: httpx.AsyncClient, parse_settings: Settings, policy_pdf: Path
):
    catalog = Catalog(parse_settings.knowledge_dir)
    csv = (TABLES / "orders.csv").read_bytes()

    response = await client.post(
        "/v1/collections/workspace/documents",
        files=upload(("orders.csv", csv), ("policy.pdf", policy_pdf.read_bytes())),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["file_ids"]) == 2 and body["job_id"]
    status = await wait(client, body["job_id"])
    assert status["status"] == "completed" and status["processed_files"] == 2
    files = details(status)
    assert {f["stage"] for f in files.values()} == {"ready"}
    assert (files["orders.csv"]["kind"], files["orders.csv"]["tables"]) == ("table", ["orders"])
    assert files["orders.csv"]["parser"] == "duckdb-csv"
    assert (files["policy.pdf"]["kind"], files["policy.pdf"]["parser"]) == ("document", "nemotron-parse-2.0")
    assert files["policy.pdf"]["chunks_created"] > 0 and files["policy.pdf"]["progress_percent"] == 100

    manifest_path = parse_settings.knowledge_dir / "catalog" / "sources" / "workspace.tables.json"
    tables_manifest = json.loads(manifest_path.read_text())
    assert list(SOURCE_SCHEMA.iter_errors(tables_manifest)) == []
    assert tables_manifest["status"] == "ready"
    assert [t["name"] for t in tables_manifest["database"]["tables"]] == ["orders"]
    assert tables_manifest["database"]["tables"][0]["time_column"] == "ordered_at"
    documents_manifest = catalog.read_source("workspace.documents")
    assert documents_manifest["documents"]["count"] == 1
    assert documents_manifest["files"][0]["pages"] == 2
    workspace = catalog.read_pack("workspace")
    assert (workspace["status"], workspace["sources"]) == ("ready", ["workspace.documents", "workspace.tables"])

    listed = (await client.get("/v1/collections/workspace/documents")).json()["files"]
    assert {f["file_name"]: f["status"] for f in listed} == {"orders.csv": "success", "policy.pdf": "success"}


async def test_reuploading_identical_bytes_returns_the_same_file(client: httpx.AsyncClient):
    csv = (TABLES / "orders.csv").read_bytes()
    first = (await client.post("/v1/collections/workspace/documents", files=upload(("orders.csv", csv)))).json()
    await wait(client, first["job_id"])

    again = (await client.post("/v1/collections/workspace/documents", files=upload(("copy.csv", csv)))).json()

    assert again["file_ids"] == first["file_ids"]
    status = await wait(client, again["job_id"])
    assert status["status"] == "completed"
    assert len((await client.get("/v1/collections/workspace/documents")).json()["files"]) == 1


async def test_deleting_a_file_drops_its_table_and_rewrites_the_manifest(
    client: httpx.AsyncClient, parse_settings: Settings
):
    files = upload(
        ("customers.csv", (TABLES / "customers.csv").read_bytes()), ("orders.csv", (TABLES / "orders.csv").read_bytes())
    )
    job = (await client.post("/v1/collections/workspace/documents", files=files)).json()
    status = await wait(client, job["job_id"])
    orders_id = details(status)["orders.csv"]["file_id"]
    catalog = Catalog(parse_settings.knowledge_dir)
    orders = next(t for t in catalog.read_source("workspace.tables")["database"]["tables"] if t["name"] == "orders")
    assert orders["foreign_keys"][0]["references_table"] == "customers"

    response = await client.request("DELETE", "/v1/collections/workspace/documents", json={"file_ids": [orders_id]})

    assert response.status_code == 200
    manifest = catalog.read_source("workspace.tables")
    assert [f["file_name"] for f in manifest["files"]] == ["customers.csv"]
    assert [t["name"] for t in manifest["database"]["tables"]] == ["customers"]
    with duckdb.connect(str(parse_settings.knowledge_dir / "sources" / "workspace.tables" / "tables.duckdb")) as db:
        assert [row[0] for row in db.execute("SELECT table_name FROM duckdb_tables()").fetchall()] == ["customers"]

    remaining = (await client.get("/v1/collections/workspace/documents")).json()["files"]
    await client.request("DELETE", "/v1/collections/workspace/documents", json={"file_ids": [remaining[0]["file_id"]]})
    assert catalog.read_source("workspace.tables") is None
    assert catalog.read_pack("workspace")["status"] == "empty"


async def test_deleting_a_document_removes_its_chunks(client: httpx.AsyncClient, parse_settings: Settings, policy_pdf):
    job = (
        await client.post("/v1/collections/workspace/documents", files=upload(("p.pdf", policy_pdf.read_bytes())))
    ).json()
    await wait(client, job["job_id"])

    await client.request("DELETE", "/v1/collections/workspace/documents", json={"file_ids": job["file_ids"]})

    index = KnowledgeIndex(parse_settings.milvus_uri, "knowledge")
    try:
        assert index.count(source_id="workspace.documents") == 0
    finally:
        index.close()
    assert not list((parse_settings.knowledge_dir / "sources").glob("workspace.documents/documents/*.md"))


async def test_a_renamed_or_empty_file_fails_alone_in_its_batch(client: httpx.AsyncClient, parse_settings: Settings):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.txt", "hello")
    files = upload(
        ("report.pdf", archive.getvalue()),
        ("empty.csv", b""),
        ("setup.exe", b"MZ\x90\x00"),
        ("orders.csv", (TABLES / "orders.csv").read_bytes()),
    )

    job = (await client.post("/v1/collections/workspace/documents", files=files)).json()

    status = await wait(client, job["job_id"])
    assert status["status"] == "completed"
    files = details(status)
    assert files["orders.csv"]["status"] == "success"
    assert (files["report.pdf"]["status"], files["report.pdf"]["error_code"]) == ("failed", "type_mismatch")
    assert "report.pdf should be a PDF file, but it holds ZIP content" in files["report.pdf"]["error_message"]
    assert (files["empty.csv"]["error_code"], files["empty.csv"]["error_message"]) == (
        "empty_file",
        "empty.csv is empty.",
    )
    assert files["setup.exe"]["error_code"] == "unsupported_type"
    manifest = Catalog(parse_settings.knowledge_dir).read_source("workspace.tables")
    assert manifest["status"] == "ready"
    assert {f["file_name"]: f["status"] for f in manifest["files"]} == {"empty.csv": "failed", "orders.csv": "ready"}


async def test_upload_limits(parse_settings: Settings, embedder, tokenizer):
    settings = replace(parse_settings, max_files=2, max_file_mb=1)
    async with running(settings, embedder, tokenizer) as client:
        too_many = upload(("a.csv", b"a\n1\n"), ("b.csv", b"b\n1\n"), ("c.csv", b"c\n1\n"))
        response = await client.post("/v1/collections/workspace/documents", files=too_many)
        assert response.status_code == 400
        assert "2 files" in response.json()["error"]["message"]

        big = b"a\n" + b"1\n" * (600 * 1024)
        job = (await client.post("/v1/collections/workspace/documents", files=upload(("big.csv", big)))).json()
        status = await wait(client, job["job_id"])
        assert details(status)["big.csv"]["error_code"] == "too_large"

        oversized = await client.post(
            "/v1/collections/workspace/documents", files=upload(("huge.csv", b"1" * 3_200_000))
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "request_too_large"

        assert (
            await client.post("/v1/collections/nope/documents", files=upload(("a.csv", b"a\n1\n")))
        ).status_code == 404
        await client.post("/v1/collections", json={"name": "session-1"})
        other = await client.post("/v1/collections/session-1/documents", files=upload(("a.csv", b"a\n1\n")))
        assert other.status_code == 400


async def test_collections(client: httpx.AsyncClient):
    listed = (await client.get("/v1/collections")).json()["collections"]
    assert [c["name"] for c in listed] == ["workspace"]
    assert {"file_count", "chunk_count", "backend", "metadata"} <= listed[0].keys()

    created = await client.post("/v1/collections", json={"name": "session-1", "description": "a chat"})
    assert created.status_code == 201 and created.json()["name"] == "session-1"
    assert (await client.post("/v1/collections", json={"name": "session-1"})).status_code == 200
    assert (await client.get("/v1/collections/session-1")).json()["description"] == "a chat"
    assert (await client.get("/v1/collections/missing")).status_code == 404

    assert (await client.delete("/v1/collections/session-1")).status_code == 200
    assert (await client.get("/v1/collections/session-1")).status_code == 404
    assert (await client.get("/v1/documents/unknown/status")).status_code == 404


async def test_a_restart_finishes_interrupted_files(parse_settings: Settings, embedder, tokenizer):
    store = JobStore(parse_settings.db_path)
    data = (TABLES / "orders.csv").read_bytes()
    file_id = "f-00000000000000aa"
    # Stored before originals kept their extension: recovery renames it, so openpyxl and DuckDB can read it.
    stored = parse_settings.knowledge_dir / "sources" / "workspace.tables" / "files" / file_id
    stored.parent.mkdir(parents=True)
    stored.write_bytes(data)
    entry = {"file_id": file_id, "file_name": "orders.csv", "source_id": "workspace.tables", "sha256": "a" * 64}
    job_id = store.create_job("workspace", [{**entry, "size_bytes": len(data), "kind": "table", "stage": "loading"}])

    async with running(parse_settings, embedder, tokenizer) as client:
        status = await wait(client, job_id)

    assert status["status"] == "completed"
    assert details(status)["orders.csv"]["tables"] == ["orders"]


async def test_pack_sync_runs_at_startup_and_reports_progress(parse_settings: Settings, embedder, tokenizer, tmp_path):
    packs_dir = tmp_path / "packs-mounted"
    shutil.copytree(FIXTURES / "packs", packs_dir)
    async with running(replace(parse_settings, packs_dir=packs_dir), embedder, tokenizer) as client:
        for _ in range(600):
            packs = (await client.get("/v1/packs/status")).json()["packs"]
            if packs and packs[0]["status"] in ("ready", "failed"):
                break
            await asyncio.sleep(0.05)
        assert packs == [{"id": "mini", "status": "ready", "files_total": 3, "files_done": 3, "error": None}]

        again = await client.post("/v1/packs/sync")
        assert again.status_code == 202


async def test_a_multi_sheet_workbook_ingests_through_the_upload_api(
    client: httpx.AsyncClient, parse_settings: Settings, tmp_path: Path
):
    workbook = write_awkward_xlsx(tmp_path / "Credit Risk Report.xlsx").read_bytes()

    job = (
        await client.post("/v1/collections/workspace/documents", files=upload(("Credit Risk Report.xlsx", workbook)))
    ).json()

    status = await wait(client, job["job_id"])
    [details_] = status["file_details"]
    assert (status["status"], details_["status"], details_["error_message"]) == ("completed", "success", None)
    assert details_["tables"] == ["credit_risk_report_q1_sales", "credit_risk_report_q2_sales"]
    assert details_["parser"] == "duckdb-xlsx"
    files = parse_settings.knowledge_dir / "sources" / "workspace.tables" / "files"
    assert [p.name for p in files.iterdir()] == [f"{details_['file_id']}.xlsx"]
    manifest = Catalog(parse_settings.knowledge_dir).read_source("workspace.tables")
    assert [t["name"] for t in manifest["database"]["tables"]] == details_["tables"]

    await client.request("DELETE", "/v1/collections/workspace/documents", json={"file_ids": job["file_ids"]})
    assert not files.exists()  # the last file took the whole source with it


async def test_a_restart_recovers_a_file_stored_without_its_extension(parse_settings: Settings, embedder, tokenizer):
    store = JobStore(parse_settings.db_path)
    data = write_awkward_xlsx(parse_settings.knowledge_dir / "awkward.xlsx").read_bytes()
    file_id = "f-00000000000000bb"
    legacy = parse_settings.knowledge_dir / "sources" / "workspace.tables" / "files" / file_id
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(data)
    entry = {"file_id": file_id, "file_name": "awkward.xlsx", "source_id": "workspace.tables", "sha256": "b" * 64}
    job_id = store.create_job("workspace", [{**entry, "size_bytes": len(data), "kind": "table", "stage": "loading"}])

    async with running(parse_settings, embedder, tokenizer) as client:
        status = await wait(client, job_id)

    assert details(status)["awkward.xlsx"]["tables"] == ["awkward_q1_sales", "awkward_q2_sales"]
    assert [p.name for p in legacy.parent.iterdir()] == [f"{file_id}.xlsx"]


async def test_a_file_interrupted_twice_fails_instead_of_running_again(parse_settings: Settings, embedder, tokenizer):
    store = JobStore(parse_settings.db_path)
    data = (TABLES / "orders.csv").read_bytes()
    file_id = "f-00000000000000cc"
    stored = parse_settings.knowledge_dir / "sources" / "workspace.tables" / "files" / f"{file_id}.csv"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(data)
    entry = {"file_id": file_id, "file_name": "orders.csv", "source_id": "workspace.tables", "sha256": "c" * 64}
    job_id = store.create_job("workspace", [{**entry, "size_bytes": len(data), "kind": "table", "stage": "loading"}])
    store.claim(file_id)  # two runs that never finished
    store.update_file(file_id, claimed=0)
    store.claim(file_id)

    async with running(parse_settings, embedder, tokenizer) as client:
        status = await wait(client, job_id)

    assert status["status"] == "failed"
    assert details(status)["orders.csv"]["error_code"] == "interrupted"


async def test_tables_are_replaced_whole_while_a_reader_holds_the_database(
    client: httpx.AsyncClient, parse_settings: Settings
):
    import subprocess
    import sys

    first = (
        await client.post(
            "/v1/collections/workspace/documents",
            files=upload(("customers.csv", (TABLES / "customers.csv").read_bytes())),
        )
    ).json()
    await wait(client, first["job_id"])
    database = parse_settings.knowledge_dir / "sources" / "workspace.tables" / "tables.duckdb"
    reader = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import duckdb, time; c = duckdb.connect({str(database)!r}, read_only=True); "
            "print('attached', flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert reader.stdout.readline().strip() == "attached"  # another process holds it read-only, as tables does

        second = (
            await client.post(
                "/v1/collections/workspace/documents",
                files=upload(("orders.csv", (TABLES / "orders.csv").read_bytes())),
            )
        ).json()
        status = await wait(client, second["job_id"], timeout=20)
    finally:
        reader.kill()
        reader.wait()

    assert details(status)["orders.csv"]["status"] == "success"
    manifest = Catalog(parse_settings.knowledge_dir).read_source("workspace.tables")
    assert sorted(t["name"] for t in manifest["database"]["tables"]) == ["customers", "orders"]
    assert sorted(p.name for p in database.parent.glob("tables.duckdb*")) == ["tables.duckdb"]


def second_pdf(policy_pdf: Path) -> bytes:
    """Other bytes (so not deduplicated), the same pages: a PDF reader ignores what follows %%EOF."""
    return policy_pdf.read_bytes() + b"\n% a second copy\n"


async def test_two_workspace_documents_parse_at_the_same_time(
    settings: Settings, embedder, tokenizer, policy_pdf: Path
):
    import threading

    both_parsing = threading.Barrier(2, timeout=10)

    def probe(settings: Settings) -> str:
        both_parsing.wait()  # passes only when the two documents are in their parsing stage together
        return "PARSE_BASE_URL is empty"

    async with running(settings, embedder, tokenizer, parse_probe=probe) as client:
        files = upload(("a.pdf", policy_pdf.read_bytes()), ("b.pdf", second_pdf(policy_pdf)))
        job = (await client.post("/v1/collections/workspace/documents", files=files)).json()
        status = await wait(client, job["job_id"])

    assert [f["status"] for f in status["file_details"]] == ["success", "success"], status["file_details"]


async def test_an_upload_returns_while_a_document_is_mid_parse(
    settings: Settings, embedder, tokenizer, policy_pdf: Path
):
    import threading

    parsing, release = threading.Event(), threading.Event()

    def probe(settings: Settings) -> str:
        parsing.set()
        release.wait(30)
        return "PARSE_BASE_URL is empty"

    async with running(settings, embedder, tokenizer, parse_probe=probe) as client:
        first = (
            await client.post("/v1/collections/workspace/documents", files=upload(("a.pdf", policy_pdf.read_bytes())))
        ).json()
        assert await asyncio.to_thread(parsing.wait, 10)
        try:
            # Another document for the same source: its manifest refresh must not wait for the first to finish.
            second = client.post(
                "/v1/collections/workspace/documents", files=upload(("notes.md", b"# Notes\n\nHello.\n"))
            )
            response = await asyncio.wait_for(second, timeout=5)
            assert response.status_code == 200
            deleted = client.request(
                "DELETE", "/v1/collections/workspace/documents", json={"file_ids": response.json()["file_ids"]}
            )
            assert (await asyncio.wait_for(deleted, timeout=5)).status_code == 200
        finally:
            release.set()
        assert (await wait(client, first["job_id"]))["status"] == "completed"


async def test_a_failed_database_copy_fails_the_file_and_leaves_no_copy(
    client: httpx.AsyncClient, parse_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    from demo_ingest import pipeline as pipeline_module

    first = (
        await client.post(
            "/v1/collections/workspace/documents",
            files=upload(("customers.csv", (TABLES / "customers.csv").read_bytes())),
        )
    ).json()
    await wait(client, first["job_id"])

    def disk_full(source: Path, target: Path) -> None:
        Path(target).write_bytes(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pipeline_module.shutil, "copyfile", disk_full)
    second = (
        await client.post(
            "/v1/collections/workspace/documents", files=upload(("orders.csv", (TABLES / "orders.csv").read_bytes()))
        )
    ).json()
    status = await wait(client, second["job_id"], timeout=20)

    orders = details(status)["orders.csv"]
    assert (orders["status"], orders["error_code"]) == ("failed", "internal_error")
    assert "No space left on device" in orders["error_message"]
    directory = parse_settings.knowledge_dir / "sources" / "workspace.tables"
    assert sorted(p.name for p in directory.glob("tables.duckdb*")) == ["tables.duckdb"]
    manifest = Catalog(parse_settings.knowledge_dir).read_source("workspace.tables")
    assert {f["file_name"]: f["status"] for f in manifest["files"]} == {
        "customers.csv": "ready",
        "orders.csv": "failed",
    }
    assert JobStore(parse_settings.db_path).get_file(orders["file_id"])["claimed"] == 0


class HeldLoads:
    """Wraps the table loader so a test can hold one file's load mid-way, and counts the loads per file name."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *held: str) -> None:
        import threading

        from demo_ingest import pipeline as pipeline_module

        self.held = set(held)
        self.loading, self.release = threading.Event(), threading.Event()
        self.calls: dict[str, int] = {}
        load = pipeline_module.tables.load_table_file

        def loader(db_path, path, file_name, existing, on_progress):
            self.calls[file_name] = self.calls.get(file_name, 0) + 1
            if file_name in self.held:
                self.loading.set()
                assert self.release.wait(30)
            return load(db_path, path, file_name, existing, on_progress)

        monkeypatch.setattr(pipeline_module.tables, "load_table_file", loader)


def table_names(database: Path) -> list[str]:
    with duckdb.connect(str(database), read_only=True) as db:
        return sorted(row[0] for row in db.execute("SELECT table_name FROM duckdb_tables()").fetchall())


async def test_an_upload_and_a_delete_return_while_another_table_file_loads(
    settings: Settings, embedder, tokenizer, monkeypatch: pytest.MonkeyPatch
):
    loads = HeldLoads(monkeypatch, "orders.csv")
    database = settings.knowledge_dir / "sources" / "workspace.tables" / "tables.duckdb"
    catalog = Catalog(settings.knowledge_dir)
    async with running(settings, embedder, tokenizer) as client:
        customers = (
            await client.post("/v1/collections/workspace/documents", files=upload(("customers.csv", b"id,name\n1,a\n")))
        ).json()
        await wait(client, customers["job_id"])
        orders = (
            await client.post(
                "/v1/collections/workspace/documents",
                files=upload(("orders.csv", (TABLES / "orders.csv").read_bytes())),
            )
        ).json()
        assert await asyncio.to_thread(loads.loading.wait, 10)
        try:
            # The orders file holds the build lock: neither request may wait for it.
            extra = client.post("/v1/collections/workspace/documents", files=upload(("extra.csv", b"x,y\n1,2\n")))
            extra = await asyncio.wait_for(extra, timeout=5)
            assert extra.status_code == 200
            deleted = client.request(
                "DELETE", "/v1/collections/workspace/documents", json={"file_ids": customers["file_ids"]}
            )
            assert (await asyncio.wait_for(deleted, timeout=5)).status_code == 200
            manifest = catalog.read_source("workspace.tables")
            assert "customers.csv" not in {f["file_name"] for f in manifest["files"]}
            assert manifest["database"]["tables"] == []  # the deleted table leaves the manifest at once
        finally:
            loads.release.set()
        await wait(client, orders["job_id"])
        await wait(client, extra.json()["job_id"])
        for _ in range(200):  # the deleted table leaves the database once the loads are done
            if "customers" not in table_names(database):
                break
            await asyncio.sleep(0.05)

    assert table_names(database) == ["extra", "orders"]
    manifest = catalog.read_source("workspace.tables")
    assert sorted(t["name"] for t in manifest["database"]["tables"]) == ["extra", "orders"]


async def test_a_failing_table_file_leaves_a_concurrent_table_load_alone(
    settings: Settings, embedder, tokenizer, monkeypatch: pytest.MonkeyPatch
):
    from demo_ingest import pipeline as pipeline_module

    loads = HeldLoads(monkeypatch, "orders.csv")
    write_source = Catalog.write_source
    failed: list[str] = []

    def fail_once(self: Catalog, manifest: dict[str, Any]) -> None:
        files = {f["file_name"]: f["status"] for f in manifest.get("files", [])}
        if files.get("customers.csv") == "ready" and not failed:
            failed.append("customers.csv")
            raise OSError("the catalog could not be written")
        write_source(self, manifest)

    log_exception = pipeline_module.logger.exception

    def handled_late(message: str, *args: Any, **kwargs: Any) -> None:
        if "could not be finished" in message:  # the failing worker handles its error while orders is mid-load
            assert loads.loading.wait(10)
        log_exception(message, *args, **kwargs)

    monkeypatch.setattr(Catalog, "write_source", fail_once)
    monkeypatch.setattr(pipeline_module.logger, "exception", handled_late)
    database = settings.knowledge_dir / "sources" / "workspace.tables" / "tables.duckdb"
    async with running(settings, embedder, tokenizer) as client:
        earlier = (
            await client.post("/v1/collections/workspace/documents", files=upload(("stores.csv", b"store,city\n1,a\n")))
        ).json()
        await wait(client, earlier["job_id"])
        customers = (
            await client.post("/v1/collections/workspace/documents", files=upload(("customers.csv", b"id,name\n1,a\n")))
        ).json()
        orders = (
            await client.post(
                "/v1/collections/workspace/documents",
                files=upload(("orders.csv", (TABLES / "orders.csv").read_bytes())),
            )
        ).json()
        assert await asyncio.to_thread(loads.loading.wait, 10)
        try:
            first = await wait(client, customers["job_id"])
        finally:
            loads.release.set()
        second = await wait(client, orders["job_id"])

    assert failed and first["file_details"][0]["error_code"] == "internal_error"
    assert second["file_details"][0]["status"] == "success"
    assert {"orders", "stores"} <= set(table_names(database))  # nothing the other files committed was lost
    manifest = Catalog(settings.knowledge_dir).read_source("workspace.tables")
    assert sorted(t["name"] for t in manifest["database"]["tables"]) == ["orders", "stores"]
    assert sorted(p.name for p in database.parent.glob("tables.duckdb*")) == ["tables.duckdb"]


async def test_two_uploads_of_the_same_bytes_at_once_run_the_file_once(
    settings: Settings, embedder, tokenizer, monkeypatch: pytest.MonkeyPatch
):
    loads = HeldLoads(monkeypatch)
    csv = (TABLES / "orders.csv").read_bytes()
    async with running(settings, embedder, tokenizer) as client:
        responses = await asyncio.gather(
            *(
                client.post("/v1/collections/workspace/documents", files=upload((name, csv)))
                for name in ("a.csv", "b.csv")
            )
        )
        for response in responses:
            assert (await wait(client, response.json()["job_id"]))["status"] == "completed"

    assert sum(loads.calls.values()) == 1


async def test_a_forced_pack_sync_ingests_a_ready_pack_again(parse_settings: Settings, embedder, tokenizer, tmp_path):
    packs_dir = tmp_path / "packs-mounted"
    shutil.copytree(FIXTURES / "packs", packs_dir)

    async def synced(client: httpx.AsyncClient) -> None:
        for _ in range(600):
            body = (await client.get("/v1/packs/status")).json()
            if not body["running"] and body["packs"] and body["packs"][0]["status"] == "ready":
                return
            await asyncio.sleep(0.05)
        raise AssertionError(body)

    async with running(replace(parse_settings, packs_dir=packs_dir), embedder, tokenizer) as client:
        await synced(client)
        embedder.calls.clear()
        assert (await client.post("/v1/packs/sync")).status_code == 202
        await synced(client)
        assert embedder.calls == []  # up to date

        assert (await client.post("/v1/packs/sync", params={"force": "true"})).status_code == 202
        await synced(client)
        assert embedder.calls != []
