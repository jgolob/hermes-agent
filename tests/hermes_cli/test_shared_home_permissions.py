"""Contract tests for ``HERMES_SHARED_HOME`` group-reviewable state."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_constants import (
    apply_shared_hermes_mode,
    enforce_shared_hermes_home,
    is_shared_hermes_home,
    shared_hermes_dir_mode,
)
from utils import atomic_json_write, atomic_yaml_write


@pytest.fixture
def shared_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SHARED_HOME", "1")
    return home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_shared_home_flag_is_posix_truthy(shared_home: Path) -> None:
    assert is_shared_hermes_home()


def test_apply_shared_mode_sets_group_reviewable_directory_and_file_modes(
    shared_home: Path,
) -> None:
    directory = shared_home / "skills"
    directory.mkdir()
    regular = directory / "SKILL.md"
    regular.write_text("# test\n", encoding="utf-8")
    executable = directory / "run.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    assert apply_shared_hermes_mode(directory, directory=True)
    assert apply_shared_hermes_mode(regular)
    assert apply_shared_hermes_mode(executable, executable=True)

    assert _mode(directory) == shared_hermes_dir_mode()
    assert _mode(regular) == 0o660
    assert _mode(executable) == 0o770


def test_shared_mode_never_changes_paths_outside_hermes_root(
    shared_home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    outside.chmod(0o600)

    assert not apply_shared_hermes_mode(outside)
    assert _mode(outside) == 0o600


def test_config_security_helpers_use_shared_modes(shared_home: Path) -> None:
    from hermes_cli import config

    directory = shared_home / "sessions"
    directory.mkdir()
    file_path = shared_home / "auth.json"
    file_path.write_text("{}\n", encoding="utf-8")

    config._secure_dir(directory)
    config._secure_file(file_path)

    assert _mode(directory) == shared_hermes_dir_mode()
    assert _mode(file_path) == 0o660


def test_shared_home_mode_overrides_legacy_directory_mode(
    shared_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermes_cli import config

    monkeypatch.setenv("HERMES_HOME_MODE", "0700")
    directory = shared_home / "logs"
    directory.mkdir()

    config._secure_dir(directory)

    assert _mode(directory) == shared_hermes_dir_mode()


def test_atomic_writers_make_new_shared_home_files_group_reviewable(
    shared_home: Path,
) -> None:
    json_path = shared_home / "state.json"
    yaml_path = shared_home / "config.yaml"

    atomic_json_write(json_path, {"ok": True})
    atomic_yaml_write(yaml_path, {"ok": True})

    assert _mode(json_path) == 0o660
    assert _mode(yaml_path) == 0o660


def test_shared_mode_intentionally_makes_credentials_group_reviewable(
    shared_home: Path,
) -> None:
    """The trusted group, not individual file types, is the security boundary."""
    from tools.mcp_oauth import _write_json

    generic_secret = shared_home / "auth.json"
    mcp_token = shared_home / "mcp-tokens" / "server.json"

    atomic_json_write(generic_secret, {"refresh_token": "secret"}, mode=0o600)
    _write_json(mcp_token, {"refresh_token": "secret"})

    assert _mode(generic_secret) == 0o660
    assert _mode(mcp_token) == 0o660
    assert _mode(mcp_token.parent) == shared_hermes_dir_mode()


def test_startup_enforcement_repairs_drift_across_entire_home(
    shared_home: Path,
) -> None:
    unexpected = shared_home / "future-state" / "nested"
    unexpected.mkdir(parents=True)
    regular = unexpected / "state.json"
    regular.write_text("{}\n", encoding="utf-8")
    executable = shared_home / "workspace" / "run.sh"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    # Reproduce the warm-restart bug: the root is already correct, while
    # descendants have drifted back to owner-only modes.
    shared_home.chmod(shared_hermes_dir_mode())
    unexpected.chmod(0o700)
    regular.chmod(0o600)
    executable.parent.chmod(0o700)
    executable.chmod(0o700)
    original_uid = executable.stat().st_uid

    assert enforce_shared_hermes_home(shared_home) == []

    assert _mode(shared_home) == shared_hermes_dir_mode()
    assert _mode(unexpected) == shared_hermes_dir_mode()
    assert _mode(regular) == 0o660
    assert _mode(executable.parent) == shared_hermes_dir_mode()
    assert _mode(executable) == 0o770
    assert executable.stat().st_uid == original_uid


def test_startup_enforcement_avoids_redundant_mode_or_group_changes(
    shared_home: Path,
) -> None:
    state = shared_home / "state.json"
    state.write_text("{}\n", encoding="utf-8")
    assert enforce_shared_hermes_home(shared_home) == []

    with (
        patch("hermes_constants.os.chmod") as chmod,
        patch("hermes_constants.os.chown") as chown,
    ):
        assert enforce_shared_hermes_home(shared_home) == []

    chmod.assert_not_called()
    chown.assert_not_called()


def test_startup_enforcement_does_not_follow_symlinks(
    shared_home: Path, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    secret = outside / "secret"
    secret.write_text("private\n", encoding="utf-8")
    secret.chmod(0o600)
    (shared_home / "external").symlink_to(outside, target_is_directory=True)

    assert enforce_shared_hermes_home(shared_home) == []

    assert _mode(outside) == 0o700
    assert _mode(secret) == 0o600


def test_startup_enforcement_reports_failures_and_continues(
    shared_home: Path,
) -> None:
    blocked = shared_home / "blocked.json"
    blocked.write_text("{}\n", encoding="utf-8")
    blocked.chmod(0o600)
    real_chmod = os.chmod

    def fail_blocked(path, mode, *, follow_symlinks=True):
        if Path(path) == blocked:
            raise PermissionError("owned by another user")
        return real_chmod(path, mode, follow_symlinks=follow_symlinks)

    with patch("hermes_constants.os.chmod", side_effect=fail_blocked):
        errors = enforce_shared_hermes_home(shared_home)

    assert len(errors) == 1
    assert str(blocked) in errors[0]
    assert "owned by another user" in errors[0]


def test_native_home_initialization_enforces_existing_descendants(
    shared_home: Path,
) -> None:
    from hermes_cli import config

    drifted = shared_home / "workspace" / "review.txt"
    drifted.parent.mkdir()
    drifted.write_text("review\n", encoding="utf-8")
    drifted.parent.chmod(0o700)
    drifted.chmod(0o600)
    config._HERMES_HOME_ENSURED.discard(str(shared_home))

    config.ensure_hermes_home()

    assert _mode(drifted.parent) == shared_hermes_dir_mode()
    assert _mode(drifted) == 0o660


def test_agent_created_skill_is_group_reviewable(shared_home: Path) -> None:
    from tools import skill_manager_tool

    skills_dir = shared_home / "skills"
    with (
        patch.object(skill_manager_tool, "SKILLS_DIR", skills_dir),
        patch("agent.skill_utils.get_all_skills_dirs", return_value=[skills_dir]),
    ):
        result = skill_manager_tool._create_skill(
            "review-test",
            "---\nname: review-test\ndescription: Review permission test.\n---\n\n"
            "# Review Test\n",
        )

    skill_file = skills_dir / "review-test" / "SKILL.md"
    assert result["success"]
    assert _mode(skill_file.parent) == shared_hermes_dir_mode()
    assert _mode(skill_file) == 0o660


def test_local_file_tool_applies_shared_mode_and_preserves_executable_intent(
    shared_home: Path,
) -> None:
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations

    hooks_dir = shared_home / "hooks"
    hooks_dir.mkdir(mode=shared_hermes_dir_mode())
    file_ops = ShellFileOperations(LocalEnvironment(cwd=str(shared_home)))

    ordinary = hooks_dir / "review.txt"
    result = file_ops.write_file(str(ordinary), "reviewable\n")
    assert result.error is None
    assert _mode(ordinary) == 0o660

    executable = hooks_dir / "review.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o770)
    result = file_ops.write_file(str(executable), "#!/bin/sh\necho reviewed\n")
    assert result.error is None
    assert _mode(executable) == 0o770


def test_local_file_tool_makes_intermediate_directories_reviewable(
    shared_home: Path,
) -> None:
    """Directories seeded by write_file's `mkdir -p` must be group-reviewable.

    Without this, a nested write leaves owner-only parents that hide an
    otherwise-0660 file from the reviewing group.
    """
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations

    file_ops = ShellFileOperations(LocalEnvironment(cwd=str(shared_home)))
    nested = shared_home / "skills" / "deep" / "nested" / "SKILL.md"

    result = file_ops.write_file(str(nested), "reviewable\n")

    assert result.error is None
    assert _mode(nested) == 0o660
    for parent in (nested.parent, nested.parent.parent, nested.parent.parent.parent):
        assert _mode(parent) == shared_hermes_dir_mode(), (
            f"{parent} is not group-reviewable"
        )


def test_shared_mode_walk_stops_at_the_hermes_root(shared_home: Path) -> None:
    """The parent walk must not widen permissions above HERMES_HOME."""
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations

    outside = shared_home.parent
    original = _mode(outside)

    file_ops = ShellFileOperations(LocalEnvironment(cwd=str(shared_home)))
    result = file_ops.write_file(str(shared_home / "logs" / "run.log"), "entry\n")

    assert result.error is None
    assert _mode(outside) == original


def test_primary_installer_config_stage_honors_shared_home(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "installed-home"
    result = subprocess.run(
        [
            "bash",
            str(repo_root / "scripts" / "install.sh"),
            "--stage",
            "config",
            "--dir",
            str(repo_root),
            "--hermes-home",
            str(home),
            "--no-skills",
            "--json",
        ],
        cwd=repo_root,
        env={**os.environ, "HERMES_SHARED_HOME": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _mode(home) == shared_hermes_dir_mode()
    assert _mode(home / ".env") == 0o660
    assert _mode(home / "skills") == shared_hermes_dir_mode()
    assert _mode(home / ".no-bundled-skills") == 0o660


def test_primary_installer_keeps_owner_only_default(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "ordinary-home"
    env = dict(os.environ)
    env.pop("HERMES_SHARED_HOME", None)
    result = subprocess.run(
        [
            "bash",
            str(repo_root / "scripts" / "install.sh"),
            "--stage",
            "config",
            "--dir",
            str(repo_root),
            "--hermes-home",
            str(home),
            "--no-skills",
            "--json",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _mode(home / ".env") == 0o600


def test_installer_and_container_manage_the_same_shared_subdirectories() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    def shared_subdirs(path: Path) -> set[str]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith('HERMES_SHARED_SUBDIRS="'):
                return set(line.removeprefix('HERMES_SHARED_SUBDIRS="').removesuffix('"').split())
        raise AssertionError(f"HERMES_SHARED_SUBDIRS not found in {path}")

    assert shared_subdirs(repo_root / "scripts" / "install.sh") == shared_subdirs(
        repo_root / "docker" / "stage2-hook.sh"
    )
