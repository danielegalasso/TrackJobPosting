"""SMTP connection and authentication, including the Gmail failure modes."""

from __future__ import annotations

import smtplib
import ssl

import pytest

from acide import mailer
from acide.models import EmailConfig


def _config(**overrides) -> EmailConfig:
    base = {
        "enabled": True,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "use_tls": True,
        "sender_email": "candidate@gmail.com",
        "sender_password": "abcdefghijklmnop",
    }
    base.update(overrides)
    return EmailConfig(**base)


class FakeSMTP:
    """Records what the mailer does, and can refuse chosen passwords."""

    instances: list[FakeSMTP] = []

    def __init__(self, host, port, timeout=None, context=None, implicit_tls=False):
        self.host = host
        self.port = port
        self.context = context
        self.implicit_tls = implicit_tls
        self.started_tls = False
        self.logins: list[tuple[str, str]] = []
        self.sent: list[object] = []
        self.quit_called = False
        #: Passwords this server will accept; None means accept anything.
        self.accepts: set[str] | None = None
        self.starttls_supported = True
        FakeSMTP.instances.append(self)

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        if not self.starttls_supported:
            raise smtplib.SMTPNotSupportedError("STARTTLS not supported")
        self.started_tls = True

    def login(self, user, password):
        self.logins.append((user, password))
        if self.accepts is not None and password not in self.accepts:
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.8 Username and Password not accepted. For more information ..."
            )

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


class _Smtp:
    """Installs FakeSMTP and lets a test shape the next server it hands out."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.configure()

    def configure(self, **attributes):
        """Every subsequent connection gets these attributes (e.g. `accepts`)."""

        def plain(host, port, timeout=None):
            server = FakeSMTP(host, port, timeout=timeout)
            for key, value in attributes.items():
                setattr(server, key, value)
            return server

        def implicit(host, port, timeout=None, context=None):
            server = FakeSMTP(host, port, timeout=timeout, context=context, implicit_tls=True)
            for key, value in attributes.items():
                setattr(server, key, value)
            return server

        self._monkeypatch.setattr(smtplib, "SMTP", plain)
        self._monkeypatch.setattr(smtplib, "SMTP_SSL", implicit)


@pytest.fixture(autouse=True)
def smtp(monkeypatch):
    FakeSMTP.instances = []
    return _Smtp(monkeypatch)


def test_port_587_negotiates_starttls():
    mailer.handshake(_config(smtp_port=587, use_tls=True))
    server = FakeSMTP.instances[-1]
    assert server.implicit_tls is False
    assert server.started_tls is True


def test_port_465_uses_implicit_tls_even_with_the_tls_box_ticked():
    """The regression: 465 is TLS-only, so it must never be spoken to in the clear.

    `use_tls` defaults to True, so the old `port == 465 and not use_tls`
    condition sent a plaintext socket at Gmail's implicit-TLS port and hung.
    """
    mailer.handshake(_config(smtp_port=465, use_tls=True))
    server = FakeSMTP.instances[-1]
    assert server.implicit_tls is True
    assert server.started_tls is False
    assert isinstance(server.context, ssl.SSLContext)


def test_port_465_uses_implicit_tls_with_the_box_unticked_too():
    mailer.handshake(_config(smtp_port=465, use_tls=False))
    assert FakeSMTP.instances[-1].implicit_tls is True


def test_plaintext_relay_when_tls_is_off_on_a_normal_port():
    mailer.handshake(_config(smtp_port=25, use_tls=False, sender_password=""))
    server = FakeSMTP.instances[-1]
    assert server.implicit_tls is False
    assert server.started_tls is False


def test_a_server_without_starttls_says_so_usefully(smtp):
    smtp.configure(starttls_supported=False)
    with pytest.raises(mailer.MailError, match="does not offer STARTTLS"):
        mailer.handshake(_config(smtp_port=587, use_tls=True))


def test_gmail_app_password_pasted_with_spaces_still_authenticates(smtp):
    """Google prints app passwords as 'abcd efgh ijkl mnop'.

    Users paste that verbatim; the server only accepts the 16 characters
    without spaces, so the login failed for a reason nothing explained.
    """
    smtp.configure(accepts={"abcdefghijklmnop"})

    detail = mailer.handshake(_config(sender_password="abcd efgh ijkl mnop"))
    assert "candidate@gmail.com" in detail

    # Tried as typed first, then compacted — on separate connections.
    assert [password for _, password in FakeSMTP.instances[0].logins] == ["abcd efgh ijkl mnop"]
    assert [password for _, password in FakeSMTP.instances[1].logins] == ["abcdefghijklmnop"]


def test_a_passphrase_containing_spaces_is_tried_as_typed_first(smtp):
    smtp.configure(accepts={"correct horse battery staple"})

    mailer.handshake(_config(sender_password="correct horse battery staple"))
    # Accepted on the first attempt, so no compacted retry happened.
    assert len(FakeSMTP.instances) == 1


def test_auth_failure_reports_the_server_reply_and_the_gmail_hint(smtp):
    smtp.configure(accepts=set())

    with pytest.raises(mailer.MailError) as caught:
        mailer.handshake(_config(sender_password="not-an-app-password"))

    message = str(caught.value)
    assert "535" in message
    assert "Username and Password not accepted" in message
    assert "app password" in message


def test_auth_failure_mentions_the_space_retry_when_one_happened(smtp):
    smtp.configure(accepts=set())

    with pytest.raises(mailer.MailError, match="also retried without the spaces"):
        mailer.handshake(_config(sender_password="abcd efgh ijkl mnop"))


def test_handshake_requires_a_sender_address():
    with pytest.raises(mailer.MailError, match="no sender address"):
        mailer.handshake(_config(sender_email=""))


def test_handshake_requires_a_server():
    with pytest.raises(mailer.MailError, match="no SMTP server"):
        mailer.handshake(_config(smtp_server=""))


def test_whitespace_around_the_host_is_tolerated():
    mailer.handshake(_config(smtp_server="  smtp.gmail.com  "))
    assert FakeSMTP.instances[-1].host == "smtp.gmail.com"


def test_handshake_describes_the_negotiated_mode():
    assert "STARTTLS" in mailer.handshake(_config(smtp_port=587, use_tls=True))
    assert "implicit TLS" in mailer.handshake(_config(smtp_port=465))
    assert "plaintext" in mailer.handshake(_config(smtp_port=25, use_tls=False))


def test_sending_uses_the_same_authentication_path(smtp):
    """The space-stripping retry must help real digests, not just the test button."""
    smtp.configure(accepts={"abcdefghijklmnop"})

    mailer.send(
        _config(sender_password="abcd efgh ijkl mnop"),
        "someone@example.com",
        "Subject",
        "<p>hi</p>",
        "hi",
    )
    assert FakeSMTP.instances[-1].sent, "the message was never handed to the server"
    assert FakeSMTP.instances[-1].quit_called is True


def test_sending_is_refused_when_email_is_disabled():
    with pytest.raises(mailer.MailError, match="disabled"):
        mailer.send(_config(enabled=False), "a@b.c", "s", "<p>h</p>", "h")
