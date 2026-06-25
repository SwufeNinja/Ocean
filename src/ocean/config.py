from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_DOTENV_VALUES: dict[str, str] = {}


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load config files. Run: pip install -e .") from exc

    config_path = Path(path)
    env = _load_effective_dotenv_env(config_path.parent)
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return expand_env(data, env=env)


def expand_env(value: Any, *, env: dict[str, str] | None = None) -> Any:
    env = os.environ if env is None else env
    if isinstance(value, dict):
        return {key: expand_env(item, env=env) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item, env=env) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: env.get(match.group(1), ""), value)
    return value


def _load_effective_dotenv_env(config_dir: Path) -> dict[str, str]:
    base_env = dict(os.environ)
    for key, value in list(_DOTENV_VALUES.items()):
        if base_env.get(key) == value:
            base_env.pop(key)

    cwd_dotenv = _read_dotenv(Path.cwd() / ".env")
    config_dotenv = _read_dotenv(config_dir / ".env")

    effective_env = dict(base_env)
    for key, value in cwd_dotenv.items():
        if key not in base_env:
            effective_env.setdefault(key, value)
    for key, value in config_dotenv.items():
        if key not in base_env:
            effective_env[key] = value

    for key in set(cwd_dotenv) | set(config_dotenv):
        if key not in base_env and key in effective_env:
            os.environ[key] = effective_env[key]
            _DOTENV_VALUES[key] = effective_env[key]
    return effective_env


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values
