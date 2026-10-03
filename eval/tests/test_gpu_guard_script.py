# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`demo.sh test gpu --perf` as a script: what it runs against the stack, with docker, uv and nvidia-smi stubbed.

The guard measures the Milvus comparison into its own file and judges that one. The Benchmark tab's file,
retrieval-benchmark.json, is written only by the one-shot of `up`, so a guard run never changes what the tab shows.
"""

import os
import shutil
import subprocess
from pathlib import Path

from support import REPO

DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_DIR/docker.log"
case "$*" in
  *" config --environment") printf 'COMPOSE_PROFILES=core,retrieval,analytics-gpu\n' ;;
  *" ps -q --status running market-analytics-gpu") echo c0ffee ;;
  *" exec -T api cat /data/active/pack.json") echo '{"id": "us-equities", "profile": "default"}' ;;
  *" exec -T api cat /data/active/retrieval-benchmark-guard.json") echo '{"measuredAt": "by the guard"}' ;;
  *" exec -T api cat /data/active/retrieval-benchmark.json") echo '{"measuredAt": "at up"}' ;;
esac
"""

UV = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_DIR/uv.log"
while [ $# -gt 0 ]; do
  if [ "$1" = --retrieval-benchmark ]; then
    cat "$2" >"$STUB_DIR/judged.json"
  fi
  shift
done
"""


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def test_the_gpu_guard_judges_its_own_milvus_measurement_and_leaves_the_served_one(tmp_path):
    root, stubs = tmp_path / "repo", tmp_path / "stubs"
    shutil.copytree(REPO / "scripts", root / "scripts")
    (root / "tools" / "market-analytics").mkdir(parents=True)
    (root / ".env").write_text("")
    stubs.mkdir()
    stub(stubs, "docker", DOCKER)
    stub(stubs, "uv", UV)
    stub(stubs, "nvidia-smi", "#!/usr/bin/env bash\n")
    env = {"PATH": f"{stubs}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(tmp_path), "STUB_DIR": str(stubs)}

    finished = subprocess.run(
        ["bash", str(root / "scripts" / "demo.sh"), "test", "gpu", "--perf"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    docker = (stubs / "docker.log").read_text().splitlines()
    assert any(line.endswith("run --rm --no-deps retrieval-benchmark benchmark --guard") for line in docker)
    assert any(line.endswith("exec -T api cat /data/active/retrieval-benchmark-guard.json") for line in docker)
    assert not [line for line in docker if "retrieval-benchmark.json" in line]  # the served file: never read
    assert (stubs / "judged.json").read_text().strip() == '{"measuredAt": "by the guard"}'
    [perf] = [line for line in (stubs / "uv.log").read_text().splitlines() if " perf " in line]
    assert "--measured-since" in perf
