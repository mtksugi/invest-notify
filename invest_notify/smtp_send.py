from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    mail_from: str
    mail_to: list[str]


def load_smtp_config_from_env() -> SmtpConfig:
    """
    AWS SES SMTP想定の環境変数:
    - SES_SMTP_HOST
    - SES_SMTP_PORT (default: 587)
    - SES_SMTP_USER
    - SES_SMTP_PASS
    - MAIL_FROM
    - MAIL_TO (comma-separated)
    """

    host = os.environ.get("SES_SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SES_SMTP_HOST is required")
    port = int(os.environ.get("SES_SMTP_PORT", "587"))
    user = os.environ.get("SES_SMTP_USER", "").strip()
    password = os.environ.get("SES_SMTP_PASS", "").strip()
    if not user or not password:
        raise RuntimeError("SES_SMTP_USER and SES_SMTP_PASS are required")

    mail_from = os.environ.get("MAIL_FROM", "").strip()
    if not mail_from:
        raise RuntimeError("MAIL_FROM is required")
    mail_to_raw = os.environ.get("MAIL_TO", "").strip()
    if not mail_to_raw:
        raise RuntimeError("MAIL_TO is required")
    mail_to = [x.strip() for x in mail_to_raw.split(",") if x.strip()]
    if not mail_to:
        raise RuntimeError("MAIL_TO is required")

    return SmtpConfig(
        host=host,
        port=port,
        username=user,
        password=password,
        mail_from=mail_from,
        mail_to=mail_to,
    )


def send_text_email(*, cfg: SmtpConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = ", ".join(cfg.mail_to)
    msg.set_content(body)

    # SES推奨: 587 + STARTTLS（または 465 SMTPS）
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30) as s:
            s.login(cfg.username, cfg.password)
            s.send_message(msg)
        return

    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as s:
        s.ehlo()
        # STARTTLSが使えるなら使う
        try:
            s.starttls()
            s.ehlo()
        except smtplib.SMTPException:
            pass
        s.login(cfg.username, cfg.password)
        s.send_message(msg)

