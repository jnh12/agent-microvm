# Contributing to agent-microvm

Thanks for your interest in contributing! This guide covers how to set up a
development environment, run the tests and linter, and open a pull request.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- AWS credentials are only needed for the live end-to-end tests; the unit tests
  run without any cloud access.

## Set up a dev environment

```bash
git clone https://github.com/jnh12/agent-microvm.git
cd agent-microvm

uv sync --extra dev        # SDK + CLI + pytest/ruff + infra deps
uv pip install ./mcp       # optional: the MCP server package
```

With `pip` instead of `uv`:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ./mcp       # optional
```

## Run the tests

```bash
uv run pytest                       # SDK / infra unit tests
uv run pytest mcp/tests -q          # MCP server unit + stdio smoke tests

# Live end-to-end tests (require deployed infra and AWS credentials):
AGENT_SANDBOX_MCP_E2E=1 uv run python mcp/tests/e2e.py
```

## Lint and format

The project uses [Ruff](https://docs.astral.sh/ruff/) (config in
[pyproject.toml](pyproject.toml)):

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
```

Please make sure `ruff check` passes before opening a pull request.

## Opening a pull request

1. Fork the repository and create a topic branch from `main`.
2. Keep changes focused; add or update tests for behavior changes.
3. Ensure the tests and linter pass locally.
4. Do not commit environment-specific values (real VPC/subnet IDs, account ARNs,
   credentials). Infrastructure config in `sandbox.yaml` should use the empty
   "reuse-or-create" defaults.
5. Open the pull request with a clear description of what changed and why.

## Reporting issues

Please include reproduction steps, the command you ran, your OS and Python
version, and any relevant error output. For security-sensitive reports, please
avoid filing a public issue.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache-2.0 License](LICENSE).
