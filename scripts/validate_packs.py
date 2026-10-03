# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check every industry pack under data/packs against data/schemas, and the rules the schemas cannot state.

    uv run --with jsonschema --with pyyaml python scripts/validate_packs.py [REPO_ROOT]

(`./scripts/demo.sh data validate` runs it.) The ingest service validates the same schemas when it syncs a pack;
this check runs without the stack, before a commit.
"""

import json
import sys
from pathlib import Path

import jsonschema
import yaml

MAX_EXAMPLES = 12


def pack_errors(pack_dir: Path, pack_schema: dict, questions_schema: dict) -> list[str]:
    errors = []
    pack = yaml.safe_load((pack_dir / "pack.yaml").read_text(encoding="utf-8"))
    questions = yaml.safe_load((pack_dir / "questions.yaml").read_text(encoding="utf-8"))
    for name, document, schema in (("pack.yaml", pack, pack_schema), ("questions.yaml", questions, questions_schema)):
        for error in jsonschema.Draft202012Validator(schema).iter_errors(document):
            errors.append(f"{name}: {'/'.join(map(str, error.absolute_path))}: {error.message}")
    if errors:
        return errors

    if pack["id"] != pack_dir.name:
        errors.append(f"pack.yaml: id {pack['id']!r} is not the directory name {pack_dir.name!r}")
    sources = {source["id"]: source for source in pack["sources"]}
    if len(sources) != len(pack["sources"]):
        errors.append("pack.yaml: source ids are not unique")
    for source in pack["sources"]:
        for pattern in source["files"]:
            if not list(pack_dir.glob(pattern)):
                errors.append(f"pack.yaml: source {source['id']}: {pattern!r} matches no file")

    ids = [question["id"] for question in questions["questions"]] + [
        conversation["id"] for conversation in questions.get("conversations", [])
    ]
    if len(set(ids)) != len(ids):
        errors.append("questions.yaml: question and conversation ids are not unique")
    question_ids = {question["id"] for question in questions["questions"]}
    for entry in questions["questions"] + questions.get("conversations", []):
        for source_id in entry["sources"]:
            if source_id not in sources:
                errors.append(f"questions.yaml: {entry['id']}: unknown source {source_id!r}")
    examples = questions.get("examples")
    if examples is not None:
        featured = {question["id"] for question in questions["questions"] if question.get("featured")}
        if not set(examples) <= question_ids:
            errors.append(f"questions.yaml: examples name unknown questions: {sorted(set(examples) - question_ids)}")
        if not featured <= set(examples):
            missing = sorted(featured - set(examples))
            errors.append(f"questions.yaml: featured questions missing from examples: {missing}")
        if len(examples) > MAX_EXAMPLES:
            errors.append(f"questions.yaml: more than {MAX_EXAMPLES} examples")
    if not any(question.get("featured") for question in questions["questions"]):
        errors.append("questions.yaml: no featured question")
    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    pack_schema = json.loads((root / "data/schemas/pack.schema.json").read_text(encoding="utf-8"))
    questions_schema = json.loads((root / "data/schemas/questions.schema.json").read_text(encoding="utf-8"))
    failed = False
    packs = sorted(path.parent for path in (root / "data/packs").glob("*/pack.yaml"))
    if not packs:
        print("no packs under data/packs", file=sys.stderr)
        return 1
    for pack_dir in packs:
        errors = pack_errors(pack_dir, pack_schema, questions_schema)
        failed |= bool(errors)
        print(f"{'FAIL' if errors else 'ok  '}  {pack_dir.name}")
        for error in errors:
            print(f"      {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
