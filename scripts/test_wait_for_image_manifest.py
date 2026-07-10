#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Behavior tests for waiting until image manifests are published."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import wait_for_image_manifest as wait


def _clock():
    now = {"value": 0.0}
    sleeps = []

    def monotonic():
        return now["value"]

    def sleep(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    return monotonic, sleep, sleeps


def test_waits_for_a_manifest_that_appears_after_the_first_check(capsys):
    image = "ghcr.io/openhands/enterprise-server:cloud-1.44.0"
    attempts = [
        wait.CommandResult(returncode=1, stdout="", stderr="MANIFEST_UNKNOWN"),
        wait.CommandResult(returncode=0, stdout="manifest", stderr=""),
    ]
    calls = []

    def inspect_image(ref):
        calls.append(ref)
        return attempts.pop(0)

    monotonic, sleep, sleeps = _clock()

    wait.wait_for_images(
        [image],
        timeout_seconds=30,
        interval_seconds=5,
        inspect_image=inspect_image,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert calls == [image, image]
    assert sleeps == [5]
    output = capsys.readouterr().out
    assert "Waiting for image manifest" in output
    assert f"Image available: {image}" in output


def test_does_not_sleep_when_the_manifest_is_already_available():
    image = "ghcr.io/openhands/runtime-api:0.3.1"
    calls = []

    def inspect_image(ref):
        calls.append(ref)
        return wait.CommandResult(returncode=0, stdout="manifest", stderr="")

    monotonic, sleep, sleeps = _clock()

    wait.wait_for_images(
        [image],
        timeout_seconds=30,
        interval_seconds=5,
        inspect_image=inspect_image,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert calls == [image]
    assert sleeps == []


def test_times_out_with_the_last_manifest_error():
    image = "ghcr.io/openhands/enterprise-server:cloud-1.44.0"

    def inspect_image(_ref):
        return wait.CommandResult(returncode=1, stdout="", stderr="MANIFEST_UNKNOWN")

    monotonic, sleep, sleeps = _clock()

    with pytest.raises(wait.ImageWaitError, match="MANIFEST_UNKNOWN"):
        wait.wait_for_images(
            [image],
            timeout_seconds=10,
            interval_seconds=5,
            inspect_image=inspect_image,
            sleep=sleep,
            monotonic=monotonic,
        )

    assert sleeps == [5, 5]


def test_times_out_with_a_clear_message_when_the_command_prints_nothing():
    image = "ghcr.io/openhands/enterprise-server:cloud-1.44.0"

    def inspect_image(_ref):
        return wait.CommandResult(returncode=17, stdout="", stderr="")

    monotonic, sleep, _sleeps = _clock()

    with pytest.raises(wait.ImageWaitError, match="command exited with code 17"):
        wait.wait_for_images(
            [image],
            timeout_seconds=0,
            interval_seconds=5,
            inspect_image=inspect_image,
            sleep=sleep,
            monotonic=monotonic,
        )


def test_timeout_error_truncates_very_long_registry_output():
    image = "ghcr.io/openhands/enterprise-server:cloud-1.44.0"
    long_error = "first" + ("x" * 2500)

    def inspect_image(_ref):
        return wait.CommandResult(returncode=1, stdout="", stderr=long_error)

    monotonic, sleep, _sleeps = _clock()

    with pytest.raises(wait.ImageWaitError) as exc_info:
        wait.wait_for_images(
            [image],
            timeout_seconds=0,
            interval_seconds=5,
            inspect_image=inspect_image,
            sleep=sleep,
            monotonic=monotonic,
        )

    message = str(exc_info.value)
    assert "first" not in message
    assert "x" * 2000 in message


def test_waits_for_each_image_reference_independently():
    images = [
        "ghcr.io/openhands/runtime-api:0.3.1",
        "ghcr.io/openhands/enterprise-server:cloud-1.44.0",
    ]
    attempts = {
        images[0]: [wait.CommandResult(returncode=0, stdout="manifest", stderr="")],
        images[1]: [
            wait.CommandResult(returncode=1, stdout="", stderr="MANIFEST_UNKNOWN"),
            wait.CommandResult(returncode=0, stdout="manifest", stderr=""),
        ],
    }
    calls = []

    def inspect_image(ref):
        calls.append(ref)
        return attempts[ref].pop(0)

    monotonic, sleep, sleeps = _clock()

    wait.wait_for_images(
        images,
        timeout_seconds=30,
        interval_seconds=5,
        inspect_image=inspect_image,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert calls == [images[0], images[1], images[1]]
    assert sleeps == [5]


def test_rejects_invalid_timing_configuration():
    with pytest.raises(ValueError, match="timeout_seconds"):
        wait.wait_for_images(
            ["ghcr.io/openhands/runtime-api:0.3.1"],
            timeout_seconds=-1,
            interval_seconds=5,
        )

    with pytest.raises(ValueError, match="interval_seconds"):
        wait.wait_for_images(
            ["ghcr.io/openhands/runtime-api:0.3.1"],
            timeout_seconds=10,
            interval_seconds=0,
        )


def test_docker_manifest_inspect_adapter_returns_command_result(monkeypatch):
    image = "ghcr.io/openhands/runtime-api:0.3.1"
    received = {}

    class Completed:
        returncode = 9
        stdout = "out"
        stderr = "err"

    def fake_run(args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(wait.subprocess, "run", fake_run)

    result = wait.inspect_with_docker(image)

    assert received["args"] == ["docker", "manifest", "inspect", image]
    assert received["kwargs"] == {
        "check": False,
        "capture_output": True,
        "text": True,
    }
    assert result == wait.CommandResult(returncode=9, stdout="out", stderr="err")


def test_main_passes_cli_arguments_to_wait_for_images(monkeypatch):
    received = {}

    def fake_wait_for_images(image_refs, *, timeout_seconds, interval_seconds):
        received["image_refs"] = image_refs
        received["timeout_seconds"] = timeout_seconds
        received["interval_seconds"] = interval_seconds

    monkeypatch.setattr(wait, "wait_for_images", fake_wait_for_images)

    exit_code = wait.main(
        [
            "--timeout-seconds",
            "12",
            "--interval-seconds",
            "3",
            "ghcr.io/openhands/runtime-api:0.3.1",
        ]
    )

    assert exit_code == 0
    assert received == {
        "image_refs": ["ghcr.io/openhands/runtime-api:0.3.1"],
        "timeout_seconds": 12,
        "interval_seconds": 3,
    }


def test_main_prints_wait_errors_to_stderr(monkeypatch, capsys):
    def fake_wait_for_images(*_args, **_kwargs):
        raise wait.ImageWaitError("manifest still missing")

    monkeypatch.setattr(wait, "wait_for_images", fake_wait_for_images)

    assert wait.main(["ghcr.io/openhands/enterprise-server:cloud-1.44.0"]) == 1
    assert "manifest still missing" in capsys.readouterr().err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
