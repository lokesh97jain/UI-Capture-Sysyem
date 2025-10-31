import types
from pathlib import Path
import textwrap

import pytest
from click.testing import CliRunner

from src.cli import cli


def write_multi_doc_yaml(tmp_path: Path) -> Path:
    y = textwrap.dedent(
        """
        version: "1"
        site: demo.app
        task: alpha
        steps:
          - action: goto
            url: "https://demo.app/alpha"
        ---
        version: "1"
        site: demo.app
        task: beta
        steps:
          - action: goto
            url: "https://demo.app/beta"
        """
    )
    p = tmp_path / "demo.yaml"
    p.write_text(y, encoding="utf-8")
    return p


def test_cli_list_with_multi_doc(tmp_path: Path, monkeypatch):
    wf = write_multi_doc_yaml(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["list", "--dir", str(tmp_path), "--no-recursive"])
    assert result.exit_code == 0
    # Should list 2 workflows
    assert "Found 2 workflow(s)" in result.output


def test_cli_validate_with_dir(tmp_path: Path):
    wf = write_multi_doc_yaml(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--dir", str(tmp_path), "--no-recursive"])
    assert result.exit_code == 0
    # Two OK lines for two docs
    assert result.output.count("OK  ") == 2


def test_cli_run_monkeypatch_engine(tmp_path: Path, monkeypatch):
    wf = write_multi_doc_yaml(tmp_path)

    # Create a dummy engine module with Engine class returning a predictable result
    fake_engine = types.ModuleType("src.core.engine")

    class FakeEngine:
        def __init__(self, settings=None, interactive_auth=True):
            self.settings = settings
            self.interactive_auth = interactive_auth

        def run_workflow(self, wf):
            return {"ok": True, "run_dir": str(tmp_path / "run")}

    fake_engine.Engine = FakeEngine

    # Inject into sys.modules so `from src.core.engine import Engine` finds our fake
    monkeypatch.setitem(__import__("sys").modules, "src.core.engine", fake_engine)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            str(wf),
            "--no-parallel",
            "--interactive-auth",
        ],
    )
    # Should succeed and report OK for both docs
    assert result.exit_code == 0
    assert "OK" in result.output

