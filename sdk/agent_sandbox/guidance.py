"""Offline, packaged VMNest operating documentation."""
from pathlib import Path
ROOT = Path(__file__).with_name("guides")
TOPICS = ("guide", "lifecycle", "agents", "automation", "configuration", "mcp", "troubleshooting", "skill")
def read(topic="guide"):
    if topic not in TOPICS:
        raise ValueError("Unknown topic. Choose: " + ", ".join(TOPICS))
    return (ROOT / (topic + ".md")).read_text(encoding="utf-8")
