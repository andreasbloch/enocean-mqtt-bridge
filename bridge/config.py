import os
import re
import yaml
from collections import defaultdict


def _resolve_env(value: str) -> str:
    """Replace ${VAR} or $VAR patterns with environment variable values."""
    if not isinstance(value, str):
        return value
    def replace(m):
        var = m.group(1) or m.group(2)
        result = os.environ.get(var)
        if result is None:
            raise ValueError(f"Environment variable '{var}' is not set")
        return result
    return re.sub(r'\$\{(\w+)\}|\$(\w+)', replace, value)


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)

    # Resolve environment variables in mqtt section
    mqtt = cfg.get("mqtt", {})
    for key in ("host", "port", "username", "password", "client_id"):
        if key in mqtt:
            mqtt[key] = _resolve_env(str(mqtt[key]))

    # Port must always be int — YAML or env var may deliver a string
    try:
        mqtt["port"] = int(mqtt["port"])
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"MQTT port is invalid: {mqtt.get('port')!r} — {e}")

    devices_by_id = defaultdict(list)
    for dev in cfg.get("devices", []):
        enocean_id = dev["enocean_id"].upper()
        devices_by_id[enocean_id].append(dev)

    cfg["devices_by_id"] = devices_by_id
    return cfg