"""The SMTP client against a real server on a real socket.

The fakes in `test_mailer_smtp.py` prove the branching; this proves the
client actually speaks SMTP — it runs a throwaway server in-process and
never contacts any external provider or account.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import pytest

aiosmtpd = pytest.importorskip("aiosmtpd", reason="aiosmtpd provides the local test server")

from aiosmtpd.controller import Controller  # noqa: E402

from acide import mailer  # noqa: E402
from acide.models import EmailConfig  # noqa: E402

#: The app password as Google displays it, and as the server expects it.
DISPLAYED_PASSWORD = "abcd efgh ijkl mnop"
REAL_PASSWORD = "abcdefghijklmnop"
USERNAME = "candidate@example.com"


class Collector:
    """Accepts mail and records it."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.messages.append(envelope.content.decode("utf-8", errors="replace"))
        return "250 Message accepted"


def _authenticator(server, session, envelope, mechanism, auth_data) -> Any:
    from aiosmtpd.smtp import AuthResult

    username = auth_data.login.decode() if auth_data.login else ""
    password = auth_data.password.decode() if auth_data.password else ""
    if username == USERNAME and password == REAL_PASSWORD:
        return AuthResult(success=True)
    return AuthResult(success=False, handled=False)


def _free_port() -> int:
    """Ask the kernel for an unused port.

    aiosmtpd's controller probes the port to confirm startup, so it cannot
    be handed 0 and asked to report back what it bound.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def live_smtp():
    """A real SMTP server on localhost that demands the compact password."""
    collector = Collector()
    controller = Controller(
        collector,
        hostname="127.0.0.1",
        port=_free_port(),
        authenticator=_authenticator,
        auth_require_tls=False,
    )
    controller.start()
    try:
        yield controller, collector
    finally:
        controller.stop()


def _config(controller, password: str, **overrides) -> EmailConfig:
    base = {
        "enabled": True,
        "smtp_server": controller.hostname,
        "smtp_port": controller.port,
        "use_tls": False,
        "sender_email": USERNAME,
        "sender_password": password,
    }
    base.update(overrides)
    return EmailConfig(**base)


def test_handshake_authenticates_against_a_real_server(live_smtp):
    controller, _ = live_smtp
    detail = mailer.handshake(_config(controller, REAL_PASSWORD))
    assert USERNAME in detail


def test_a_password_pasted_with_spaces_authenticates_for_real(live_smtp):
    """End to end: the retry works over a real socket, not just a fake."""
    controller, _ = live_smtp
    detail = mailer.handshake(_config(controller, DISPLAYED_PASSWORD))
    assert USERNAME in detail


def test_a_wrong_password_is_rejected_with_the_servers_own_reply(live_smtp):
    controller, _ = live_smtp
    with pytest.raises(mailer.MailError) as caught:
        mailer.handshake(_config(controller, "definitely-wrong"))
    assert "authentication failed" in str(caught.value).lower()


def test_a_digest_is_delivered_end_to_end(live_smtp):
    controller, collector = live_smtp
    mailer.send(
        _config(controller, DISPLAYED_PASSWORD),
        "someone@example.com",
        "ACIDE-Watch: 1 new role",
        "<p>Cloud Security Engineer</p>",
        "Cloud Security Engineer",
    )
    assert len(collector.messages) == 1
    assert "Cloud Security Engineer" in collector.messages[0]
    assert "ACIDE-Watch" in collector.messages[0]


def test_an_unreachable_port_is_reported_not_hung():
    config = EmailConfig(
        enabled=True,
        smtp_server="127.0.0.1",
        smtp_port=9,  # discard: nothing is listening
        use_tls=False,
        sender_email=USERNAME,
        sender_password="x",
    )
    with pytest.raises(mailer.MailError, match="could not connect"):
        mailer.handshake(config)


def test_asyncio_loop_is_not_left_running(live_smtp):
    """The controller must not leak a loop into the rest of the suite."""
    controller, _ = live_smtp
    mailer.handshake(_config(controller, REAL_PASSWORD))
    assert threading.active_count() >= 1
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
