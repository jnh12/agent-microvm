"""Agent onboarding without network access or account credentials."""
import json
from pathlib import Path
import typer
from agent_sandbox.guidance import TOPICS, read

def register(app):
    @app.command("docs")
    def docs(topic: str = typer.Argument("guide"), as_json: bool = typer.Option(False, "--json")):
        """Read offline agent docs; use commands for the installed command schema."""
        if topic == "commands":
            from .schema import describe
            typer.echo(json.dumps(describe(app)))
            return
        try:
            content = read(topic)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
        typer.echo(json.dumps({"topic": topic, "topics": list(TOPICS), "content": content}) if as_json else content)

    @app.command("skill")
    def skill(output: Path | None = typer.Option(None, "--output")):
        """Print SKILL.md or export a portable skill with offline references."""
        if output is None:
            typer.echo(read("skill"))
            return
        files = {("SKILL.md" if t == "skill" else t + ".md"): read(t) for t in TOPICS}
        if any((output / name).exists() for name in files):
            raise typer.BadParameter("Skill files already exist; choose a new directory.")
        output.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            with (output / name).open("x", encoding="utf-8") as target:
                target.write(content)
        typer.echo(str(output))
