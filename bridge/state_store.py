import json
import logging
import os


class StateStore:
    """
    Persistenter State-Speicher für Taster-Aktoren (Light / Switch)

    - speichert letzte bekannte Zustände
    - lädt sie beim Neustart
    - FHEM-äquivalent (statefile)
    """

    def __init__(self, path="/app/state/state.json"):
        self.path = path
        self.state = {}

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                self.state = json.load(f)
            logging.info("StateStore loaded from %s", self.path)
        except FileNotFoundError:
            logging.info("StateStore file not found, starting fresh")
            self.state = {}
        except Exception as e:
            logging.error("StateStore load failed: %s", e)
            self.state = {}

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.state, f)
            logging.debug("StateStore saved")
        except Exception as e:
            logging.error("StateStore save failed: %s", e)

    def get(self, device_name, default=None):
        return self.state.get(device_name, default)

    def set(self, device_name, value):
        self.state[device_name] = value
        self.save()