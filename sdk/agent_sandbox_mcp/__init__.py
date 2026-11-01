"""agent-sandbox MCP server.

A FastMCP server that exposes the :mod:`agent_sandbox` SDK (isolated microVM
sandboxes on AWS Lambda MicroVMs) as Model Context Protocol tools and resources.

Mirrors the tool-naming conventions and response patterns of microsandbox-mcp,
implemented in Python with FastMCP. See ``README.md`` for the tool catalog and
the "Unsupported capabilities" section for reference tools not implemented here.
"""

__version__ = "0.1.2"
