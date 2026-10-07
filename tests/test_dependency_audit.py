"""Exercise CI audit reporting with real exit codes and generated reports."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from certbot.compat import os

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "report_dependency_audit.py"
FINDING = {"id": "TEST-ADVISORY", "fix_versions": ["2.0"]}


def run_report(tmp_path, version, exit_code, payload):
    report = tmp_path / "audit.json"
    if payload is not None:
        report.write_text(json.dumps(payload), encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--report",
            str(report),
            "--python-version",
            version,
            "--exit-code",
            str(exit_code),
        ],
        env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)),
        capture_output=True,
        text=True,
    )
    return result, summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"])
def test_vulnerabilities_only_nonblocking_on_python39(tmp_path, version):
    payload = {"dependencies": [{"name": "example", "version": "1.0", "vulns": [FINDING]}]}
    result, summary = run_report(tmp_path, version, 1, payload)
    assert result.returncode == (0 if version == "3.9" else 1)
    assert ("::warning::" if version == "3.9" else "::error::") in result.stdout
    assert "TEST-ADVISORY" in result.stdout
    assert "| example | 1.0 | TEST-ADVISORY | 2.0 |" in summary
    assert summary.startswith("Existing summary\n")


@pytest.mark.parametrize("version", ["3.9", "3.13"])
def test_clean_audit(tmp_path, version):
    result, summary = run_report(
        tmp_path, version, 0, {"dependencies": [{"name": "example", "version": "1", "vulns": []}]}
    )
    assert result.returncode == 0
    assert "0 vulnerability records in 0 packages" in summary
    assert "::error::" not in result.stdout
    assert "::warning::" not in result.stdout


@pytest.mark.parametrize(
    "payload,exit_code",
    [
        (None, 1),
        ({}, 1),
        ({"dependencies": None}, 1),
        ({"dependencies": [{"name": "example", "skip_reason": "Unavailable"}]}, 1),
        ({"dependencies": [{"vulns": None}]}, 1),
        ({"dependencies": []}, 1),
        ({"dependencies": []}, 2),
        ({"dependencies": [{"name": "example", "version": "1", "vulns": [FINDING]}]}, 0),
    ],
)
def test_audit_errors_are_not_accepted_as_legacy_findings(tmp_path, payload, exit_code):
    result, summary = run_report(tmp_path, "3.9", exit_code, payload)
    assert result.returncode == 1
    assert "::error::Dependency audit failed" in result.stdout
    assert "::warning::" not in result.stdout
    assert summary == "Existing summary\n"
