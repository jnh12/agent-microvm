"""Readable agent event output for ordinary terminal commands."""

from rich.console import Console
from rich.markdown import Markdown

from .output import details, label


class EventRenderer:
    def __init__(self, console=None):
        self.console = console or Console(highlight=False)
        self.streaming = False
        self.pending_text = ""
        self.approvals = set()

    def end_stream(self):
        if self.streaming:
            text, self.pending_text = self.pending_text, ""
            self.streaming = False
            self.reply(text)

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
            self.console.print(
                f"Use vmnest agent approve COMPUTER --approval {identifier} [--deny]", markup=False
            )
        elif message.get("method") == "item/agentMessage/delta":
            # Publish complete messages once, preserving Markdown and links.
            self.streaming = True
            self.pending_text += message.get("params", {}).get("delta", "")
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
                    self.pending_text = item.get("text") or self.pending_text
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
