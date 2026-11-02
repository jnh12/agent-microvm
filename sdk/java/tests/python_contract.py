"""Generate/check shared contracts from the installed Python SDK (no AWS calls)."""
import argparse
import ast
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from agent_sandbox.models import IdlePolicy, SandboxState
from agent_sandbox.infra.config import InfraConfig
from agent_sandbox.infra.resources import _default_hooks
from agent_sandbox.infra.state import InfraStateStore
from agent_sandbox.cli.state import SandboxRecord, StateStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "src/test/resources/python-contract.json"

def public_api():
    result = {}
    for module, name in [("sandbox", "Sandbox"), ("agent_client", "AgentClient"), ("control_plane", "ControlPlane")]:
        source = ROOT.parent / "agent_sandbox" / (module + ".py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
        result[name] = sorted(node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"))
    return result

def contract():
    states = ["PENDING", "STARTING", "RUNNING", "SUSPENDING", "SUSPENDED", "RESUMING", "TERMINATING", "TERMINATED", "FAILED", "other", "running", ""]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = StateStore(path)
        store.put(SandboxRecord("python", "vm-python", "example.com", "image", "us-east-1"))
        return {"public_api": public_api(), "config": asdict(InfraConfig()), "hooks": _default_hooks(), "idle": IdlePolicy().to_aws(),
                "states": {s: SandboxState.from_aws_status(s).value for s in states},
                "registry": json.loads(path.read_text())}

def check_java():
    directory = ROOT / "build/python-contract"
    sandbox_store = StateStore(directory / "state.json")
    record = sandbox_store.require("java")
    sandbox_store.put(record)
    assert json.loads((directory / "state.json").read_text())["java_agent_ports"]["java"] == 8080
    assert record.microvm_id == "vm-java" and record.region == "us-east-1"
    store = InfraStateStore(directory / "infra.json")
    state = store.load("contract", "dev")
    assert state.outputs["image_arn"] == "image-java"
    assert state.resources["image"].id == "image-java"
    assert state.resources["image"].managed is False
    print("Python read Java sandbox and infrastructure records successfully")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check-java", action="store_true")
    args = parser.parse_args()
    if args.check_java:
        check_java()
    elif args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(contract(), indent=2) + "\n", encoding="utf-8")
    else:
        assert json.loads(FIXTURE.read_text(encoding="utf-8")) == contract(), "Python contract changed; update fixture and Java behavior together"
        print("Python contract matches checked-in fixture")
