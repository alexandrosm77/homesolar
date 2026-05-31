from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import smtplib

from homesolar.config import EmailConfig


def build_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    text_body: str,
    html_body: str,
    images: dict[str, bytes] | None = None,
) -> EmailMessage:
    """Build a multipart/alternative message with optional inline PNG images.

    ``images`` maps a content-id token (without angle brackets) to PNG bytes.
    Reference them in HTML as ``<img src="cid:token">``.
    """
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()

    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    if images:
        html_part = message.get_payload()[1]
        for cid, payload in images.items():
            html_part.add_related(payload, maintype="image", subtype="png", cid=f"<{cid}>")

    return message


def send_message(config: EmailConfig, message: EmailMessage) -> None:
    with smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds) as client:
        if config.use_tls:
            client.starttls()
        client.send_message(message)
