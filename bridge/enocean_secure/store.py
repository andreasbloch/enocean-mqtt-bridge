import json
import logging
import os
from typing import Dict

from bridge.enocean_secure.context import SecureContext


class SecureStore:
    """
    Persistenter Speicher für EnOcean Secure Contexts.
    """

    def __init__(self, path="/app/state/secure_state.json"):
        self.path = path
        self.contexts: Dict[str, SecureContext] = {}

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)

            for enocean_id, data in raw.items():
                ctx = SecureContext(
                    enocean_id=enocean_id,
                    enabled=data.get("enabled", False),
                    eep=data.get("eep"),
                    key=bytes.fromhex(data["key"]) if data.get("key") else None,
                    mac_algo=data.get("mac_algo"),
                    rlc_algo=data.get("rlc_algo"),
                    rlc_counter=data.get("rlc_counter"),
                    confirm=data.get("confirm", False),
                )
                self.contexts[enocean_id] = ctx

            logging.info("[SECURE] Loaded %d secure contexts", len(self.contexts))

        except FileNotFoundError:
            logging.info("[SECURE] No secure_state.json found (fresh start)")
        except Exception as e:
            logging.error("[SECURE] Load failed: %s", e)

    def save(self):
        data = {}
        for enocean_id, ctx in self.contexts.items():
            if not ctx.enabled:
                continue

            data[enocean_id] = {
                "enabled": ctx.enabled,
                "eep": ctx.eep,
                "key": ctx.key.hex() if ctx.key else None,
                "mac_algo": ctx.mac_algo,
                "rlc_algo": ctx.rlc_algo,
                "rlc_counter": ctx.rlc_counter,
                "confirm": ctx.confirm,
            }
            ctx.mark_clean()

        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

        logging.debug("[SECURE] secure_state.json saved")

    def get(self, enocean_id: str) -> SecureContext | None:
        return self.contexts.get(enocean_id)

    def register(self, ctx: SecureContext):
        self.contexts[ctx.enocean_id] = ctx
        self.save()