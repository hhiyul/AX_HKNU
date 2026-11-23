"""Block external sockets; Windows asyncio needs a local socket pair."""

import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded_connect(sock, address):
        try:
            # The event loop's internal wakeup socket pair connects on loopback.
            allowed = isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError("Unit tests must not connect to external networks")
        return real_connect(sock, address)

    def blocked(*args, **kwargs):
        raise AssertionError("Unit tests must not create network connections")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
