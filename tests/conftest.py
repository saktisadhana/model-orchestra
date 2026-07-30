"""Default pytest isolation for deterministic, zero-network checks."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_offline_state(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep unmarked tests off the network and away from the real budget ledger."""
    import server

    monkeypatch.setattr(server, "BUDGET_STATE_PATH", tmp_path / "budget.json")
    monkeypatch.setattr(server, "BUDGET_DB_PATH", tmp_path / "budget.sqlite3")
    monkeypatch.setattr(server, "DOGFOOD_DB_PATH", tmp_path / "dogfood.sqlite3")
    if request.node.get_closest_marker("network") is not None:
        return

    real_connect = socket.socket.connect

    def blocked_connect(sock, address):
        host = address[0] if isinstance(address, tuple) and address else None
        if host in {"127.0.0.1", "::1", "localhost"}:
            return real_connect(sock, address)
        raise RuntimeError("outbound network is disabled in the offline pytest suite")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)

    real_create_connection = socket.create_connection
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, *args, **kwargs: (
            real_create_connection(address, *args, **kwargs)
            if isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1", "localhost"}
            else (_ for _ in ()).throw(
                RuntimeError("outbound network is disabled in the offline pytest suite")
            )
        ),
    )
    real_connect_ex = socket.socket.connect_ex

    def blocked_connect_ex(sock, address):
        host = address[0] if isinstance(address, tuple) and address else None
        if host in {"127.0.0.1", "::1", "localhost"}:
            return real_connect_ex(sock, address)
        raise RuntimeError("outbound network is disabled in the offline pytest suite")

    monkeypatch.setattr(socket.socket, "connect_ex", blocked_connect_ex)
