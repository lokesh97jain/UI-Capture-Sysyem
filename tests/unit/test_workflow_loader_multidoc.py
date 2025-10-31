from pathlib import Path
import textwrap

from src.core.workflow_loader import load_workflows_file


def test_load_workflows_file_multiple_docs(tmp_path: Path):
    yml = textwrap.dedent(
        """
        version: "1"
        site: example.com
        task: do_one
        steps:
          - action: goto
            url: "https://example.com/one"
        ---
        version: "1"
        site: example.com
        task: do_two
        steps:
          - action: goto
            url: "https://example.com/two"
        """
    )
    f = tmp_path / "multi.yaml"
    f.write_text(yml, encoding="utf-8")

    workflows = load_workflows_file(f)
    assert len(workflows) == 2
    assert workflows[0].site == "example.com" and workflows[0].task == "do_one"
    assert workflows[1].site == "example.com" and workflows[1].task == "do_two"

