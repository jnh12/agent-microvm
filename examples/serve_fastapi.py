"""Serve a FastAPI app from inside a MicroVM sandbox and port-forward to localhost.

End-to-end test of the "run a web app in the box, reach it from my laptop" flow:

1. Create a sandbox (image/role read from env or flags).
2. Write a small FastAPI todo app into the VM at ``/work/main.py``.
3. Launch ``uvicorn`` *detached* on port 8000 inside the VM. ``Sandbox.exec`` is
   one-shot/blocking, so a long-running server must be backgrounded or the call
   would hang until timeout.
4. Wait until the app answers *inside* the VM.
5. Mint an auth token scoped to the app port (``allowedPorts=[{"port": 8000}]``).
6. Run a tiny local reverse proxy on ``http://localhost:8000`` that injects the
   ``X-aws-proxy-auth`` / ``X-aws-proxy-port`` headers the ingress requires, so a
   plain browser/curl request works.

Requires deployed infra (see README):

    export AGENT_SANDBOX_IMAGE_ARN=$(asb infra output image_arn)
    export AGENT_SANDBOX_EXECUTION_ROLE_ARN=$(asb infra output execution_role_arn)
    python examples/serve_fastapi.py

Then open http://localhost:8000/docs in your browser, or:

    curl localhost:8000/todos
    curl -X POST localhost:8000/todos -H 'content-type: application/json' \
        -d '{"title": "buy milk"}'

Press Ctrl+C to tear everything down (terminates the MicroVM).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from agent_sandbox import Sandbox

# Surface control-plane retry warnings (e.g. transient RunMicrovm failures).
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

APP_PORT = 8000  # port uvicorn binds *inside* the VM
LOCAL_PORT = 8000  # port the local reverse proxy binds on your laptop
WORKDIR = "/work"

# A minimal FastAPI todo app. In a real flow this is what a coding agent
# (Claude Code / Cursor) would have generated into the VM's filesystem.
FASTAPI_APP = '''\
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="todo")
_todos: list[dict] = []


class Todo(BaseModel):
    title: str
    done: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/todos")
def list_todos() -> list[dict]:
    return _todos


@app.post("/todos")
def add_todo(todo: Todo) -> dict:
    item = {"id": len(_todos) + 1, **todo.model_dump()}
    _todos.append(item)
    return item
'''

# Start uvicorn detached so the (blocking) exec call returns while the server
# keeps running. ``Sandbox.exec`` waits on the process's stdout/stderr pipes
# (agentd calls proc.communicate()), so a plain ``uvicorn ... &`` would leave
# those pipes open and hang the request. Spawning via Python with
# ``start_new_session=True`` + ``close_fds`` (default) and std streams pointed at
# files/devnull guarantees uvicorn does NOT inherit the exec pipes, so the launch
# call returns immediately. Bind 0.0.0.0 so the ingress proxy can reach it.
LAUNCH_PY = (
    "import subprocess;"
    "p = subprocess.Popen("
    f"['uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '{APP_PORT}'],"
    f" cwd='{WORKDIR}',"
    f" stdout=open('{WORKDIR}/server.log', 'w'),"
    " stderr=subprocess.STDOUT,"
    " stdin=subprocess.DEVNULL,"
    " start_new_session=True);"
    f"open('{WORKDIR}/server.pid', 'w').write(str(p.pid));"
    "print('launched', p.pid)"
)

# Poll readiness from inside the VM (python is always present; curl may not be).
READY_CMD = (
    "import urllib.request, sys; "
    f"urllib.request.urlopen('http://localhost:{APP_PORT}/health', timeout=2); "
    "print('ready')"
)


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    return endpoint.rstrip("/")


class _TokenHolder:
    """Thread-safe-enough holder for the current auth token.

    The reverse proxy runs in a worker thread and reads ``value``; the asyncio
    loop refreshes it. Rebinding a ``str`` attribute is atomic in CPython, so a
    lock is unnecessary for this read-mostly single-writer pattern.
    """

    def __init__(self, value: str) -> None:
        self.value = value


def _make_handler(base_url: str, token: _TokenHolder, verify: bool):
    client = httpx.Client(base_url=base_url, verify=verify, timeout=30.0)
    hop_by_hop = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
    }

    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:  # quieter output
            pass

        def _proxy(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None

            fwd_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in {"host", "content-length", "connection"}
            }
            fwd_headers["X-aws-proxy-auth"] = token.value
            fwd_headers["X-aws-proxy-port"] = str(APP_PORT)

            try:
                upstream = client.request(
                    self.command, self.path, content=body, headers=fwd_headers
                )
            except httpx.HTTPError as exc:
                self.send_error(502, f"upstream error: {exc}")
                return

            self.send_response(upstream.status_code)
            for key, val in upstream.headers.items():
                if key.lower() in hop_by_hop:
                    continue
                self.send_header(key, val)
            content = upstream.content
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        # BaseHTTPRequestHandler dispatches on do_<METHOD>.
        do_GET = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_DELETE = _proxy
        do_PATCH = _proxy
        do_HEAD = _proxy
        do_OPTIONS = _proxy

    return ProxyHandler


async def _mint_token(sandbox: Sandbox) -> str:
    return await sandbox._cp.create_auth_token(  # noqa: SLF001 - example script
        sandbox.microvm_id,
        expiration_in_minutes=30,
        allowed_ports=[{"port": APP_PORT}, {"port": sandbox._config.agent_port}],  # noqa: SLF001
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="fastapi-demo", help="sandbox name")
    parser.add_argument("--image", default=None, help="image ARN (or $AGENT_SANDBOX_IMAGE_ARN)")
    parser.add_argument("--role", default=None, help="execution role ARN (or $AGENT_SANDBOX_EXECUTION_ROLE_ARN)")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument("--local-port", type=int, default=LOCAL_PORT, help="local proxy port")
    parser.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="skip TLS verification to the MicroVM endpoint (debugging only)",
    )
    args = parser.parse_args()

    verify_tls = not args.no_verify_tls

    print(f"[1/6] creating sandbox {args.name!r} ...")
    sandbox = await Sandbox.create(
        args.name,
        image_arn=args.image,
        execution_role_arn=args.role,
        region=args.region,
        verify_tls=verify_tls,
    )
    print(f"      microvm_id={sandbox.microvm_id} endpoint={sandbox.endpoint}")

    server: ThreadingHTTPServer | None = None
    try:
        print(f"[2/6] writing FastAPI app to {WORKDIR}/main.py ...")
        await sandbox.write_file(f"{WORKDIR}/main.py", FASTAPI_APP.encode("utf-8"))

        print(f"[3/6] launching uvicorn (detached) on VM port {APP_PORT} ...")
        launch = await sandbox.exec("python", ["-c", LAUNCH_PY], cwd=WORKDIR)
        if not launch.success:
            raise RuntimeError(f"launch failed: {launch.stderr_text or launch.stdout_text}")
        print(f"      {launch.stdout_text.strip()}")

        print("[4/6] waiting for the app to answer inside the VM ...")
        for attempt in range(30):
            probe = await sandbox.exec("python", ["-c", READY_CMD])
            if probe.success and "ready" in probe.stdout_text:
                print(f"      app is up after {attempt + 1} attempt(s)")
                break
            await asyncio.sleep(1)
        else:
            log = await sandbox.exec("cat", [f"{WORKDIR}/server.log"])
            raise RuntimeError(f"app never became ready. server.log:\n{log.stdout_text}")

        print(f"[5/6] minting auth token scoped to port {APP_PORT} ...")
        token = _TokenHolder(await _mint_token(sandbox))

        print(f"[6/6] starting local reverse proxy on http://localhost:{args.local_port} ...")
        base_url = _normalize_endpoint(sandbox.endpoint or "")
        handler = _make_handler(base_url, token, verify=verify_tls)
        server = ThreadingHTTPServer(("127.0.0.1", args.local_port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        print(
            "\n"
            f"  Ready! The FastAPI app in the sandbox is now at:\n"
            f"    http://localhost:{args.local_port}/docs\n"
            f"    curl localhost:{args.local_port}/todos\n"
            f"    curl -X POST localhost:{args.local_port}/todos "
            f"-H 'content-type: application/json' -d '{{\"title\": \"buy milk\"}}'\n\n"
            "  Press Ctrl+C to tear down (terminates the MicroVM).\n"
        )

        # Trigger graceful shutdown on Ctrl+C (SIGINT) or SIGTERM so the finally
        # block below always terminates the MicroVM instead of leaking it.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # e.g. Windows
                loop.add_signal_handler(sig, stop.set)

        # Keep alive; refresh the token before its 30-min TTL expires.
        while not stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=25 * 60)
            if stop.is_set():
                break
            print("[token] refreshing auth token ...")
            token.value = await _mint_token(sandbox)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\nshutting down ...")
        if server is not None:
            server.shutdown()
        print("terminating sandbox ...")
        await sandbox.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
