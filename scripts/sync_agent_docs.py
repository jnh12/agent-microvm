"""Sync canonical packaged guides to API and website checkouts."""
import argparse
import shutil
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--service", type=Path, required=True)
parser.add_argument("--site", type=Path, required=True)
a = parser.parse_args()
source = Path(__file__).resolve().parents[1] / "sdk/agent_sandbox"
shutil.copytree(source / "guides", a.service / "vmnest_service/guides", dirs_exist_ok=True)
shutil.copy2(source / "guidance.py", a.service / "vmnest_service/guidance.py")
dest = a.site / "outputs/hooklet/dist"
(dest / "skills/vmnest").mkdir(parents=True, exist_ok=True)
for guide in (source / "guides").glob("*.md"):
    if guide.name != "skill.md":
        shutil.copy2(guide, dest / "skills/vmnest" / guide.name)
shutil.copy2(source / "guides/skill.md", dest / "skills/vmnest/SKILL.md")
shutil.copy2(source / "guides/guide.md", dest / "agent-guide.txt")
