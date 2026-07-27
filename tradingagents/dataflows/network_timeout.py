"""Process-wide network timeout defaults for third-party data providers."""

from __future__ import annotations

from functools import wraps
import socket
from typing import Any

import requests


def install_default_network_timeout(timeout_seconds: float) -> None:
    """Apply a finite timeout to raw sockets and requests calls without one.

    AKShare and several vendor SDKs call ``requests`` internally without
    exposing a timeout argument. ``socket.setdefaulttimeout`` alone is not
    sufficient because urllib3 resets request sockets to blocking mode when it
    receives ``timeout=None``. Wrapping ``Session.send`` covers both public
    requests helpers and SDK-created sessions while preserving explicit
    per-request timeouts.
    """
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("network timeout must be greater than zero")

    socket.setdefaulttimeout(timeout)

    current_send = requests.sessions.Session.send
    if getattr(current_send, "_ta_default_timeout_wrapper", False):
        current_send._ta_default_timeout_seconds = timeout
        return

    @wraps(current_send)
    def send_with_default_timeout(
        session: requests.Session,
        request: requests.PreparedRequest,
        **kwargs: Any,
    ):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = send_with_default_timeout._ta_default_timeout_seconds
        return current_send(session, request, **kwargs)

    send_with_default_timeout._ta_default_timeout_wrapper = True
    send_with_default_timeout._ta_default_timeout_seconds = timeout
    requests.sessions.Session.send = send_with_default_timeout
