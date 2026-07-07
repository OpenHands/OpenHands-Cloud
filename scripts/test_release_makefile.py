"""Tests for release Makefile packaging hygiene."""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"


def copy_release_makefile_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MAKEFILE, project / "Makefile")
    (project / "charts" / "openhands" / "charts" / "runtime-api").mkdir(
        parents=True
    )
    (project / "replicated").mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "test-channel"],
        cwd=project,
        check=True,
    )
    return project


def run_make(project: Path, target: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            target,
            "VERSION=0.0.0",
            "BRANCH=test-channel",
            "CHANNEL=test-channel",
            *args,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


def add_archive_file(archive: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def write_chart_archive(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for index, name in enumerate(names):
            add_archive_file(archive, name, f"content {index}")


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_clean_removes_stale_dependency_archives_before_release(tmp_path: Path) -> None:
    project = copy_release_makefile_project(tmp_path)
    stale_archive = (
        project / "charts" / "openhands" / "charts" / "runtime-api-0.3.10.tgz"
    )
    stale_archive.write_bytes(b"stale runtime-api dependency")
    build_archive = project / "build" / "openhands-0.9.0.tgz"
    build_archive.parent.mkdir()
    build_archive.write_bytes(b"old package")

    result = run_make(project, "clean")

    assert result.returncode == 0, combined_output(result)
    assert not build_archive.exists()
    assert not stale_archive.exists()
    assert (project / "charts" / "openhands" / "charts" / "runtime-api").is_dir()


def test_chart_archive_guard_rejects_duplicate_paths(tmp_path: Path) -> None:
    project = copy_release_makefile_project(tmp_path)
    release_archive = project / "build" / "openhands-0.9.0.tgz"
    write_chart_archive(
        release_archive,
        [
            "openhands/Chart.yaml",
            "openhands/charts/runtime-api/values.yaml",
            "openhands/charts/runtime-api/values.yaml",
        ],
    )

    result = run_make(
        project,
        "check-duplicate-chart-entries",
        f"RELEASE_FILES={release_archive}",
    )

    output = combined_output(result)
    assert result.returncode != 0
    assert "contains duplicate archive paths" in output
    assert "openhands/charts/runtime-api/values.yaml" in output


def test_chart_archive_guard_accepts_unique_paths(tmp_path: Path) -> None:
    project = copy_release_makefile_project(tmp_path)
    release_archive = project / "build" / "openhands-0.9.0.tgz"
    write_chart_archive(
        release_archive,
        [
            "openhands/Chart.yaml",
            "openhands/charts/runtime-api/values.yaml",
            "openhands/charts/automation/values.yaml",
        ],
    )

    result = run_make(
        project,
        "check-duplicate-chart-entries",
        f"RELEASE_FILES={release_archive}",
    )

    assert result.returncode == 0, combined_output(result)
