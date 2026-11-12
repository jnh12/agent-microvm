"""Terminal chat presentation; agent execution remains in the hosted API."""

import sys
import threading
from contextlib import nullcontext
from urllib.parse import quote

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from agent_sandbox.accounts import AccountError

from .output import details, label

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
HELP = (
    "/queue TEXT  Queue a message while busy\n"
    "/cancel      Cancel the active run\n"
    "/status      Show the current run\n"
    "/allow ID    Approve a pending request\n"
    "/deny ID     Deny a pending request\n"
    "/exit        Leave chat; remote work keeps running\n"
    "/help        Show these commands"
)


class EventRenderer:
    def __init__(self, console=None):
        self.console = console or Console(highlight=False)
        self.streaming = False
        self.approvals = set()

    def end_stream(self):
        if self.streaming:
            self.console.print()
            self.streaming = False

    def reply(self, text):
        if text:
            self.console.print("\nAgent", style="bold green")
            self.console.print(Markdown(text))

    def render(self, event, skip_prompt=False):
        message = event.get("data") or {}
        if event.get("type") == "prompt":
            if not skip_prompt:
                self.end_stream()
                self.console.print("\nYou", style="bold cyan")
                self.console.print(message.get("text", ""), markup=False)
        elif event.get("type") == "approval":
            identifier = message.get("id", "")
            if identifier in self.approvals:
                return
            self.approvals.add(identifier)
            self.end_stream()
            details(message, title="Approval requested")
            self.console.print(f"/allow {identifier}  or  /deny {identifier}", markup=False)
        elif message.get("method") == "item/agentMessage/delta":
            if not self.streaming:
                self.console.print("\nAgent", style="bold green")
                self.streaming = True
            self.console.print(Text(message.get("params", {}).get("delta", "")), end="")
        elif message.get("type") == "assistant":
            self.end_stream()
            for part in message.get("message", {}).get("content", []):
                if part.get("type") == "text":
                    self.reply(part["text"])
                elif part.get("type") == "tool_use":
                    self.console.print(
                        "Working · " + part.get("name", "tool"), style="dim", markup=False
                    )
        elif message.get("method") in ("item/started", "item/completed"):
            item = message.get("params", {}).get("item", {})
            if item.get("type") == "agentMessage" and message["method"] == "item/completed":
                if self.streaming:
                    self.end_stream()
                else:
                    self.reply(item.get("text", ""))
            elif message["method"] == "item/started" and item.get("type") not in (
                "agentMessage",
                "userMessage",
                "reasoning",
            ):
                self.end_stream()
                self.console.print(
                    "Working · " + label(item.get("type", "tool")), style="dim", markup=False
                )
        elif event.get("type") == "error":
            self.end_stream()
            self.console.print(
                str(message.get("message", "Agent run failed")), style="red", markup=False
            )


class ChatObserver:
    def __init__(self, api, path, history, console, stop, local_runs):
        self.api, self.path, self.history = api, path, history
        self.console, self.stop, self.local_runs = console, stop, local_runs
        self.ignored = None
        self.cursors, self.renderers, self.finished = {}, {}, set()
        self.status = "Ready"

    def poll(self):
        items = self.api("GET", self.path)["items"]
        if self.ignored is None:
            completed = [r["id"] for r in items if r["status"] in TERMINAL]
            self.ignored = set(completed[self.history :])
        active = [r for r in items if r["status"] not in TERMINAL]
        self.status = label(active[0]["status"]) if active else "Ready"
        for item in reversed(items):
            identifier = item["id"]
            if self.stop.is_set():
                return
            if identifier in self.ignored or identifier in self.finished:
                continue
            page = self.api(
                "GET",
                "/v1/agent-runs/"
                + quote(identifier, safe="")
                + "/events?after="
                + str(self.cursors.get(identifier, 0)),
            )
            if self.stop.is_set():
                return
            renderer = self.renderers.setdefault(identifier, EventRenderer(self.console))
            for event in page["items"]:
                renderer.render(event, skip_prompt=identifier in self.local_runs)
            self.cursors[identifier] = page["cursor"]
            if item["status"] in TERMINAL and not page["items"]:
                renderer.end_stream()
                self.console.print(label(item["status"]), style="dim", markup=False)
                if item.get("error"):
                    self.console.print(item["error"], style="red", markup=False)
                self.finished.add(identifier)
                self.renderers.pop(identifier, None)


def make_prompt(status):
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        "You › ",
        history=InMemoryHistory(),
        key_bindings=bindings,
        bottom_toolbar=lambda: status() + "  ·  Enter sends  ·  Esc+Enter newline  ·  /help",
        multiline=False,
        enable_history_search=True,
    )


def run_chat(name, session, history, model=None):
    from .agent_runs import api, endpoint, poll_interval, resolve, submit

    interval = poll_interval()
    terminal = sys.stdin.isatty() and sys.stdout.isatty()
    console = Console(highlight=False)
    stop = threading.Event()
    local_runs = set()
    observer = ChatObserver(
        api,
        endpoint(name) + "?session=" + quote(session, safe=""),
        history,
        console,
        stop,
        local_runs,
    )
    prompt = make_prompt(lambda: observer.status) if terminal else None
    console.print(f"VMNest · {name} · {session}", style="bold cyan", markup=False)
    with console.status("Opening computer..."):
        api("POST", "/v1/vms/" + quote(name, safe="") + "/open")
    console.print("/help for commands. /exit or Ctrl+D leaves remote work running.", style="dim")
    # Validate the connection before opening input; do not leave a broken prompt behind.
    observer.poll()
    connection_error = False

    def observe():
        nonlocal connection_error
        while not stop.wait(interval):
            try:
                observer.poll()
                if connection_error and not stop.is_set():
                    console.print("Reconnected.", style="green")
                connection_error = False
            except AccountError as exc:
                if not stop.is_set() and not connection_error:
                    console.print(
                        str(exc) + "\nReconnecting; /exit still works.",
                        style="yellow",
                        markup=False,
                    )
                connection_error = True
                observer.status = "Disconnected"
            except Exception:
                if not stop.is_set():
                    console.print("Could not read agent output. Exit and reopen chat.", style="red")
                observer.status = "Output unavailable"
                return
            if prompt:
                prompt.app.invalidate()

    worker = threading.Thread(target=observe, daemon=True)
    with patch_stdout(raw=True) if terminal else nullcontext():
        worker.start()
        try:
            while True:
                try:
                    text = prompt.prompt() if prompt else input("You › ")
                except EOFError:
                    break
                except KeyboardInterrupt:
                    console.print(
                        "Input cleared. Use /cancel to stop the agent, or /exit to leave.",
                        style="dim",
                    )
                    continue
                text = text.strip()
                if not text:
                    continue
                if text == "/exit":
                    break
                try:
                    if text == "/help":
                        console.print(HELP, markup=False)
                    elif text == "/status":
                        details(resolve(name, session=session), title="Agent status")
                    elif text == "/cancel":
                        current = resolve(name, session=session)
                        api("POST", "/v1/agent-runs/" + current["id"] + "/cancel")
                        console.print("Cancellation requested.", style="yellow")
                    elif text.startswith(("/allow ", "/deny ")):
                        current = resolve(name, session=session)
                        command, approval = text.split(" ", 1)
                        api(
                            "POST",
                            "/v1/agent-runs/" + current["id"] + "/approval",
                            {
                                "approval_id": approval.strip(),
                                "decision": "allow" if command == "/allow" else "deny",
                            },
                        )
                        console.print("Approval response saved.")
                    elif text.startswith("/") and not text.startswith("/queue "):
                        console.print("Unknown command. Use /help.", style="yellow")
                    else:
                        queued = text.startswith("/queue ")
                        message = text[7:].strip() if queued else text
                        if not message:
                            console.print("Provide a message after /queue.", style="yellow")
                            continue
                        result = submit(
                            name,
                            message,
                            session,
                            queued,
                            **({"model": model} if model is not None else {}),
                        )
                        local_runs.add(result["id"])
                        observer.status = label(result["status"])
                        console.print(label(result["status"]), style="dim")
                except AccountError as exc:
                    console.print(str(exc), style="red", markup=False)
                    if exc.code in ("AGENT_BUSY", "VM_BUSY") or "busy" in str(exc).lower():
                        console.print(
                            "Use /queue TEXT to queue a message, or /cancel to stop current work."
                        )
        finally:
            stop.set()
            worker.join(timeout=2)
    console.print("Left chat. Remote work keeps running.", style="dim")
