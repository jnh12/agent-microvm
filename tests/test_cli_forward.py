"""Tests for `asb forward`: auto-disconnect status logic and WebSocket relay."""

from __future__ import annotations

import socket
import threading
from email.message import Message
from unittest.mock import Mock

import pytest
from agent_sandbox.cli.main import (
    _is_websocket_upgrade,
    _merge_websocket_protocols,
    _parse_upstream_addr,
    _relay_bidirectional,
    _vm_is_gone,
    _websocket_handshake_and_relay,
)


@pytest.mark.parametrize(
    "status",
    ["RUNNING", "running", "PENDING", "STARTING", "CREATING", "unknown", None, ""],
)
def test_vm_is_gone_keeps_serving_for_live_or_transient(status):
    assert _vm_is_gone(status) is False


@pytest.mark.parametrize(
    "status",
    [
        "SUSPENDED",
        "SUSPENDING",
        "STOPPING",
        "STOPPED",
        "TERMINATING",
        "TERMINATED",
        "FAILED",
        "suspended",
        "  Terminated  ",
    ],
)
def test_vm_is_gone_triggers_disconnect_for_terminal_states(status):
    assert _vm_is_gone(status) is True


def _headers(**kwargs: str) -> Message:
    msg = Message()
    for key, val in kwargs.items():
        msg[key.replace("_", "-")] = val
    return msg


@pytest.mark.parametrize(
    "connection,upgrade",
    [
        ("Upgrade", "websocket"),
        ("upgrade", "websocket"),
        ("keep-alive, Upgrade", "WebSocket"),
        ("Upgrade, keep-alive", "WEBSOCKET"),
        (" Upgrade ", " websocket "),
    ],
)
def test_is_websocket_upgrade_true_for_valid_handshake(connection, upgrade):
    headers = _headers(Connection=connection, Upgrade=upgrade)
    assert _is_websocket_upgrade(headers) is True


@pytest.mark.parametrize(
    "headers",
    [
        _headers(),
        _headers(Connection="keep-alive"),
        _headers(Upgrade="websocket"),  # no Connection: Upgrade
        _headers(Connection="Upgrade", Upgrade="h2c"),  # HTTP/2 upgrade, not WS
        _headers(Connection="keep-alive", Upgrade="websocket"),
    ],
)
def test_is_websocket_upgrade_false_for_non_websocket_requests(headers):
    assert _is_websocket_upgrade(headers) is False


@pytest.mark.parametrize(
    "existing,extra,expected",
    [
        (None, ("a", "b"), "a, b"),
        ("", ("a",), "a"),
        ("vite-hmr", ("a", "b"), "vite-hmr, a, b"),
        ("a, b", ("a", "c"), "a, b, c"),
        ("  a  ,  b  ", (), "a, b"),
    ],
)
def test_merge_websocket_protocols(existing, extra, expected):
    assert _merge_websocket_protocols(existing, *extra) == expected


@pytest.mark.parametrize(
    "base_url,expected",
    [
        (
            "https://abc.lambda-microvm.us-east-1.on.aws",
            ("abc.lambda-microvm.us-east-1.on.aws", 443, True),
        ),
        ("http://127.0.0.1:8080", ("127.0.0.1", 8080, False)),
        ("https://host:8443", ("host", 8443, True)),
        ("http://host", ("host", 80, False)),
    ],
)
def test_parse_upstream_addr(base_url, expected):
    assert _parse_upstream_addr(base_url) == expected


def test_relay_bidirectional_pumps_both_directions_and_returns_on_close():
    client_near, client_far = socket.socketpair()
    upstream_near, upstream_far = socket.socketpair()

    t = threading.Thread(target=_relay_bidirectional, args=(client_far, upstream_near))
    t.start()

    client_near.sendall(b"hello-from-client")
    assert upstream_far.recv(4096) == b"hello-from-client"

    upstream_far.sendall(b"hello-from-upstream")
    assert client_near.recv(4096) == b"hello-from-upstream"

    client_near.close()
    upstream_far.close()
    t.join(timeout=5)
    assert not t.is_alive()


def _serve_once(server_sock: socket.socket, respond: bytes, result: dict) -> None:
    conn, _ = server_sock.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += conn.recv(4096)
    result["request"] = request
    conn.sendall(respond)
    result["conn"] = conn


def test_websocket_handshake_and_relay_forwards_101_and_relays_frames():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    result: dict = {}
    server_thread = threading.Thread(
        target=_serve_once,
        args=(
            server_sock,
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            b"post-handshake-payload",
            result,
        ),
    )
    server_thread.start()

    client_near, client_far = socket.socketpair()
    handler = Mock()
    handler.command = "GET"
    handler.path = "/ws"
    handler.headers = _headers(
        Host="localhost",
        Connection="Upgrade",
        Upgrade="websocket",
        **{"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13"},
    )
    handler.connection = client_far

    relay_thread = threading.Thread(
        target=_websocket_handshake_and_relay,
        kwargs=dict(
            handler=handler,
            base_url=f"http://127.0.0.1:{port}",
            verify=True,
            auth_token="tok123",
            remote_port=9000,
        ),
    )
    relay_thread.start()
    server_thread.join(timeout=5)

    # The client sees the upstream's 101 response, then the payload the fake
    # upstream sent right after it, relayed straight through.
    received = b""
    client_near.settimeout(5)
    while len(received) < len(b"HTTP/1.1 101") + 100:
        chunk = client_near.recv(4096)
        if not chunk:
            break
        received += chunk
        if b"post-handshake-payload" in received:
            break
    assert received.startswith(b"HTTP/1.1 101 Switching Protocols")
    assert b"post-handshake-payload" in received

    # Bidirectional: bytes from the client reach the fake upstream too.
    client_near.sendall(b"ping-from-client")
    assert result["conn"].recv(4096) == b"ping-from-client"

    # The auth token and target port travelled as subprotocols, not headers.
    request = result["request"]
    assert b"Sec-WebSocket-Protocol:" in request
    assert b"lambda-microvms.authentication.tok123" in request
    assert b"lambda-microvms.port.9000" in request
    handler.send_error.assert_not_called()

    client_near.close()
    result["conn"].close()
    server_sock.close()
    relay_thread.join(timeout=5)
    assert not relay_thread.is_alive()


def test_websocket_handshake_and_relay_forwards_non_101_and_stops():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    result: dict = {}
    server_thread = threading.Thread(
        target=_serve_once,
        args=(server_sock, b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n", result),
    )
    server_thread.start()

    client_near, client_far = socket.socketpair()
    handler = Mock()
    handler.command = "GET"
    handler.path = "/ws"
    handler.headers = _headers(Host="localhost", Connection="Upgrade", Upgrade="websocket")
    handler.connection = client_far

    _websocket_handshake_and_relay(
        handler=handler,
        base_url=f"http://127.0.0.1:{port}",
        verify=True,
        auth_token="tok123",
        remote_port=9000,
    )
    server_thread.join(timeout=5)

    client_near.settimeout(2)
    received = client_near.recv(4096)
    assert received.startswith(b"HTTP/1.1 403 Forbidden")
    handler.send_error.assert_not_called()

    client_near.close()
    result["conn"].close()
    server_sock.close()


def test_websocket_handshake_and_relay_reports_connection_failure():
    # Bind and immediately close to get a port nothing is listening on.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    handler = Mock()
    handler.command = "GET"
    handler.path = "/ws"
    handler.headers = _headers(Host="localhost", Connection="Upgrade", Upgrade="websocket")

    _websocket_handshake_and_relay(
        handler=handler,
        base_url=f"http://127.0.0.1:{port}",
        verify=True,
        auth_token="tok123",
        remote_port=9000,
    )

    handler.send_error.assert_called_once()
    args = handler.send_error.call_args[0]
    assert args[0] == 502


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
