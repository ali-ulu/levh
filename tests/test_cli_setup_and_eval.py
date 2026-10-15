"""In-process coverage for the two CLI commands the demo removal touched.

``cmd_setup`` and ``cmd_eval_run`` are exercised end to end through a
subprocess elsewhere (``test_setup_cli.py``), which never enters the Python
function, so ``diff-cover`` sees every line in them as uncovered. These call
the functions directly on the same real paths.
"""

from __future__ import annotations

import json

import pytest


def _args(argv: list[str]):
    from server.cli_parsers import build_parser

    parser, _ = build_parser("levh")
    return parser.parse_args(argv)


def test_setup_command_prepares_an_empty_store(tmp_path, monkeypatch, capsys):
    from server.commands.diagnostics import cmd_setup

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LEVH_ONBOARDING_RECEIPT_PATH", str(tmp_path / "onboarding-receipt.json")
    )

    assert cmd_setup(_args(["setup", "--client", "cursor", "--profile", "minimal"])) == 0

    out = capsys.readouterr().out
    assert "LEVH setup" in out
    assert "Mode:" not in out
    assert "Demo data" not in out
    assert (tmp_path / ".stackmemory" / "mcp" / "cursor-minimal.json").exists()
    receipt = json.loads((tmp_path / "onboarding-receipt.json").read_text(encoding="utf-8"))
    assert "demo_mode" not in receipt
    assert receipt["mcp_profile"] == "minimal"


def test_setup_status_prints_the_computed_readiness(tmp_path, monkeypatch, capsys):
    from server.commands.diagnostics import cmd_setup

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "LEVH_ONBOARDING_RECEIPT_PATH", str(tmp_path / "onboarding-receipt.json")
    )

    assert cmd_setup(_args(["setup", "--status"])) == 0

    status = json.loads(capsys.readouterr().out)
    assert status["memory_count"] == 0
    assert "demo_seeded" not in status
    assert "demo_memory_count" not in status


@pytest.fixture
def one_fixture(tmp_path):
    directory = tmp_path / "fixtures"
    directory.mkdir()
    (directory / "01_smoke.json").write_text(
        json.dumps(
            {
                "name": "smoke",
                "memories": [
                    {
                        "key": "m1",
                        "content": "The staging database migration finished cleanly.",
                        "source": "cli",
                        "expected_admission": "admit",
                    }
                ],
                "queries": [
                    {
                        "query": "Did the staging database migration finish cleanly?",
                        "expected_memory_keys": ["m1"],
                    }
                ],
                "expected_conflict_count": 0,
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_eval_run_command_writes_the_report(one_fixture, tmp_path, capsys):
    from server.commands.quality import cmd_eval_run

    target = tmp_path / "eval_report.json"
    args = _args(
        ["eval", "run", "--fixtures", str(one_fixture), "--output", str(target)]
    )

    assert cmd_eval_run(args) == 0

    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["fixture_count"] == 1
    assert [f["name"] for f in report["fixtures"]] == ["smoke"]
    assert "product" not in report
