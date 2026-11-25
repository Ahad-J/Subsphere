"""Core configuration and environment settings for SubSphere 4.0."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    app_env: str
    database_url: str
    app_secret: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    litellm_url: str
    litellm_master_key: str
    openmeter_url: str
    kafka_bootstrap_servers: str
    whatsapp_api_url: str
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    data_dir: str


_PLACEHOLDER_SMTP_USERS = {"your.email@gmail.com", ""}
_PLACEHOLDER_SMTP_PASSWORDS = {"your_16_char_app_password", ""}


def get_settings() -> Settings:
    smtp_host = os.getenv("SMTP_HOST", "mailpit")
    smtp_port = int(os.getenv("SMTP_PORT", "1025"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM_EMAIL", "noreply@subsphere.local")

    # Automatically fall back to built-in Mailpit relay if .env still contains example placeholders
    if (smtp_user in _PLACEHOLDER_SMTP_USERS or smtp_pass in _PLACEHOLDER_SMTP_PASSWORDS) and smtp_host == "smtp.gmail.com":
        smtp_host = "mailpit"
        smtp_port = 1025
        smtp_user = ""
        smtp_pass = ""
        smtp_from = "relay@mask.subsphere.local"

    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        database_url=os.getenv("DATABASE_URL", ""),
        app_secret=os.getenv("APP_SECRET", "dev-only-secret-change-in-production"),
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_user,
        smtp_password=smtp_pass,
        smtp_from_email=smtp_from,
        minio_endpoint=os.getenv("MINIO_ENDPOINT", ""),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", ""),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", ""),
        minio_bucket=os.getenv("MINIO_BUCKET", "subsphere-private"),
        litellm_url=os.getenv("LITELLM_URL", ""),
        litellm_master_key=os.getenv("LITELLM_MASTER_KEY", ""),
        openmeter_url=os.getenv("OPENMETER_URL", ""),
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", ""),
        whatsapp_api_url=os.getenv("WHATSAPP_API_URL", ""),
        whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        data_dir=os.getenv("SUBSPHERE_DATA_DIR", "/tmp/subsphere_data"),
    )
