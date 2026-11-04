"""SMTP transport. No credentials are written to the database or error output."""
import os
import smtplib
import ssl
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class Settings:
    user: str
    password: str = field(default="", repr=False)
    host: str = "smtp.gmail.com"
    port: int = 465
    security: str = "ssl"

    @property
    def local(self):
        return self.host in {"127.0.0.1", "::1"}

    @classmethod
    def load(cls):
        host = os.environ.get("CRM_SMTP_HOST", "smtp.gmail.com")
        security = os.environ.get("CRM_SMTP_SECURITY", "ssl")
        user = os.environ.get("CRM_SMTP_USER", "")
        password = os.environ.get("CRM_SMTP_PASSWORD", "")
        if not password and user and sys.platform == "darwin" and host not in {"127.0.0.1", "::1"}:
            try:
                result = subprocess.run(
                    ["security", "find-generic-password", "-s", "outreach-crm-gmail", "-a", user, "-w"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                if result.returncode == 0:
                    password = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Could not read Keychain; set CRM_SMTP_PASSWORD instead.") from None
        settings = cls(user, password, host, int(os.environ.get("CRM_SMTP_PORT", "587" if security == "starttls" else "465")), security)
        settings.validate()
        return settings

    def validate(self):
        if self.security not in {"ssl", "starttls", "plain"}:
            raise ValueError("CRM_SMTP_SECURITY must be ssl, starttls or plain.")
        if self.security == "plain" and not self.local:
            raise ValueError("Unencrypted SMTP is allowed only on a literal loopback address.")
        if not self.local and (not self.user or not self.password):
            raise ValueError("Set CRM_SMTP_USER and an app password in Keychain or CRM_SMTP_PASSWORD.")
        if not 1 <= self.port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535.")


class DeliveryError(Exception):
    def __init__(self, uncertain):
        self.uncertain = uncertain
        super().__init__("Delivery uncertain; check the mailbox before resolving this attempt." if uncertain
                         else "SMTP connection, authentication or delivery was rejected; check settings.")


def deliver(message, settings):
    settings.validate()
    client = None
    attempted = False
    try:
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=20, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=20)
            client.ehlo()
            if settings.security == "starttls":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
        if not settings.local:
            client.login(settings.user, settings.password)
        attempted = True
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    except (OSError, smtplib.SMTPException) as exc:
        # Explicit rejection means no acceptance. A disconnect during DATA may have delivered.
        rejected = isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError))
        raise DeliveryError(attempted and not rejected) from None
    finally:
        if client is not None:
            # A failed QUIT after server acceptance must not change success into a retry.
            client.close()
