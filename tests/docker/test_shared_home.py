"""Runtime contract tests for the shared, group-auditable Docker home."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.docker.conftest import docker_exec, wait_for_container_ready


pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="numeric host-GID bind-mount semantics are Linux-specific",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_shared_home_preserves_host_owner_and_makes_agent_skill_reviewable(
    built_image: str, container_name: str,
) -> None:
    host_data = Path(tempfile.mkdtemp(prefix="hermes-shared-"))
    original_uid = host_data.stat().st_uid
    host_gid = os.getgid()
    try:
        subprocess.run(
            [
                "docker", "run", "-d", "--name", container_name,
                "-e", "HERMES_SHARED_HOME=1",
                "-e", f"HERMES_GID={host_gid}",
                "-v", f"{host_data}:/opt/data",
                built_image, "sleep", "infinity",
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        wait_for_container_ready(container_name)

        assert host_data.stat().st_uid == original_uid
        assert host_data.stat().st_gid == host_gid
        assert _mode(host_data) == 0o2770

        code = (
            "from tools.skill_manager_tool import _atomic_write_text;"
            "from pathlib import Path;"
            "p=Path('/opt/data/skills/review-test/SKILL.md');"
            "_atomic_write_text(p, '---\\nname: review-test\\n"
            "description: test\\n---\\n')"
        )
        result = docker_exec(
            container_name,
            "/opt/hermes/.venv/bin/python",
            "-c",
            code,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        skill_file = host_data / "skills" / "review-test" / "SKILL.md"
        assert _mode(skill_file.parent) == 0o2770
        assert _mode(skill_file) == 0o660

        # Root is already correct, but descendants drift to owner-only. A warm
        # restart must repair the entire tree, including an unexpected path.
        drift = docker_exec(
            container_name,
            "sh",
            "-c",
            "mkdir -p /opt/data/future-state/nested && "
            "printf state > /opt/data/future-state/nested/state.txt && "
            "chmod 700 /opt/data/future-state /opt/data/future-state/nested && "
            "chmod 600 /opt/data/future-state/nested/state.txt",
            timeout=30,
        )
        assert drift.returncode == 0, drift.stderr
        drifted_file = host_data / "future-state" / "nested" / "state.txt"
        drifted_uid = drifted_file.stat().st_uid

        subprocess.run(
            ["docker", "restart", container_name],
            check=True,
            capture_output=True,
            timeout=60,
        )
        wait_for_container_ready(container_name)

        assert _mode(host_data) == 0o2770
        assert _mode(drifted_file.parent.parent) == 0o2770
        assert _mode(drifted_file.parent) == 0o2770
        assert _mode(drifted_file) == 0o660
        assert drifted_file.stat().st_uid == drifted_uid
        assert drifted_file.stat().st_gid == host_gid
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=10,
        )
        # The container wrote as a different UID, so the host user may not be
        # able to unlink those files directly — empty the tree from inside a
        # container first, then remove the (now empty) mount point.
        subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{host_data}:/clean",
                "--entrypoint", "sh", built_image,
                "-c", "rm -rf /clean/* /clean/.[!.]* /clean/..?* 2>/dev/null; true",
            ],
            capture_output=True,
            timeout=30,
        )
        # ignore_errors so a cleanup failure never masks the real assertion.
        shutil.rmtree(host_data, ignore_errors=True)
