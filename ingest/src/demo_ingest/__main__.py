# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""demo-ingest serve | sync-packs"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .settings import Settings

PORT = 8330


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="demo-ingest",
        description="NVIDIA Knowledge Foundation ingestion: uploads and industry packs into the knowledge catalog.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="serve the HTTP API (one uvicorn worker) and sync the packs in the background"
    )
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=PORT)
    commands.add_parser("sync-packs", help="ingest every pack under PACKS_DIR whose digest changed, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if settings.milvus_uri.endswith(".db"):
        # pymilvus parses MILVUS_URI when it is first imported and rejects a Milvus Lite path; the URI is passed to
        # every client explicitly, so the variable is no longer needed.
        os.environ.pop("MILVUS_URI", None)

    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1, timeout_graceful_shutdown=5)
    else:
        from .packs import sync_cli

        sys.exit(sync_cli(settings))


if __name__ == "__main__":
    main()
