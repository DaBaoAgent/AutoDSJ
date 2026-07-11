from __future__ import annotations

import json
import os
from pathlib import Path
from cryptography.fernet import Fernet

from .schemas import AppSettings


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
USER_CONFIG = CONFIG_DIR / "user_config.json"
ENV_FILE = ROOT / ".env"
SECRET_FILE = CONFIG_DIR / "secrets.bin"
SECRET_KEY_FILE = CONFIG_DIR / ".secret.key"
MASK = "••••••••"

KEY_MAP = {
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "siliconflow_api_key": "SILICONFLOW_API_KEY",
}


def read_env() -> dict[str, str]:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text("utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return {**values, **os.environ}


def _fernet() -> Fernet:
    CONFIG_DIR.mkdir(exist_ok=True)
    if not SECRET_KEY_FILE.exists():
        SECRET_KEY_FILE.write_bytes(Fernet.generate_key())
    return Fernet(SECRET_KEY_FILE.read_bytes())


def read_secrets() -> dict[str, str]:
    if not SECRET_FILE.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(SECRET_FILE.read_bytes()).decode("utf-8"))
    except Exception:
        return {}


def write_secrets(values: dict[str, str]) -> None:
    SECRET_FILE.write_bytes(_fernet().encrypt(json.dumps(values).encode("utf-8")))


def load_settings(mask_keys: bool = True) -> AppSettings:
    CONFIG_DIR.mkdir(exist_ok=True)
    data = json.loads(USER_CONFIG.read_text("utf-8")) if USER_CONFIG.exists() else {}
    settings = AppSettings.model_validate(data)
    env = read_env()
    secrets = read_secrets()
    for field, env_name in KEY_MAP.items():
        if not getattr(settings.api, field):
            setattr(settings.api, field, secrets.get(field) or env.get(env_name, ""))
        if mask_keys and getattr(settings.api, field):
            setattr(settings.api, field, MASK)
    return settings


def save_settings(settings: AppSettings) -> AppSettings:
    old = load_settings(mask_keys=False)
    secrets: dict[str, str] = {}
    for field in KEY_MAP:
        if getattr(settings.api, field) in ("", MASK):
            setattr(settings.api, field, getattr(old.api, field))
        if getattr(settings.api, field):
            secrets[field] = getattr(settings.api, field)
    write_secrets(secrets)
    safe_data = settings.model_dump()
    for field in KEY_MAP:
        safe_data["api"][field] = ""
    CONFIG_DIR.mkdir(exist_ok=True)
    USER_CONFIG.write_text(json.dumps(safe_data, ensure_ascii=False, indent=2), "utf-8")
    return load_settings(mask_keys=True)


def safe_settings_dump(settings: AppSettings, *, indent: int | None = 2) -> str:
    data = settings.model_dump()
    for field in KEY_MAP:
        data["api"][field] = ""
    return json.dumps(data, ensure_ascii=False, indent=indent)


def runtime_env(settings: AppSettings) -> dict[str, str]:
    saved = load_settings(mask_keys=False)
    result = os.environ.copy()
    for field, env_name in KEY_MAP.items():
        value = getattr(settings.api, field)
        if value in ("", MASK):
            value = getattr(saved.api, field)
        if value:
            result[env_name] = value
    return result
