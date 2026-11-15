"""Context access across supported Typer releases."""

try:
    from typer._click.globals import get_current_context
except ImportError:
    from click import get_current_context

__all__ = ["get_current_context"]
