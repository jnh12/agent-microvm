import json
from typer.testing import CliRunner
from agent_sandbox.cli.main import app
from agent_sandbox.guidance import TOPICS, read

def test_docs_offline_and_reject_path_traversal():
    runner = CliRunner()
    for topic in TOPICS:
        result = runner.invoke(app, ["docs", topic, "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["content"] == read(topic)
    assert runner.invoke(app, ["docs", "../accounts"]).exit_code != 0
    commands = runner.invoke(app, ["docs", "commands"])
    assert commands.exit_code == 0
    assert "cli" in json.loads(commands.output)

def test_export_skill_preserves_existing_files(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["skill", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "SKILL.md").read_text().startswith("---")
    assert (tmp_path / "guide.md").is_file()
    (tmp_path / "guide.md").write_text("user content")
    assert runner.invoke(app, ["skill", "--output", str(tmp_path)]).exit_code != 0
    assert (tmp_path / "guide.md").read_text() == "user content"
