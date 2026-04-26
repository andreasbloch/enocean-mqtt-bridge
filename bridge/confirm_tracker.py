"""
ConfirmTracker — wartet nach einem Secure-Switch-Befehl auf F6-Bestätigung.

Kommt keine Bestätigung innerhalb des Timeouts:
  → TeachIn automatisch senden
  → Befehl einmal wiederholen

Lifecycle:
  1. enocean_sender._send_switch_secure() ruft tracker.expect(enocean_id, command, tx_cb, teachin_cb)
  2. enocean_receiver empfängt F6 → ruft tracker.confirm(enocean_id)
  3. Bei Timeout: teachin_cb(), tx_cb() werden in einem Background-Thread ausgeführt
"""

import logging
import threading
import time


class ConfirmTracker:

    TIMEOUT_SEC = 3.0   # Sekunden auf F6 warten

    def __init__(self):
        self._lock    = threading.Lock()
        self._pending = {}   # enocean_id → {command, tx_cb, teachin_cb, deadline, retried}

    def expect(
        self,
        enocean_id: str,
        command: str,
        tx_cb,        # callable() → resend the switch command
        teachin_cb,   # callable() → send teachin
    ):
        """Register expectation of F6 feedback within TIMEOUT_SEC."""
        deadline = time.monotonic() + self.TIMEOUT_SEC
        with self._lock:
            self._pending[enocean_id] = {
                "command":    command,
                "tx_cb":      tx_cb,
                "teachin_cb": teachin_cb,
                "deadline":   deadline,
                "retried":    False,
            }
        # Start timeout watcher
        t = threading.Thread(
            target=self._watch,
            args=(enocean_id, deadline),
            daemon=True,
        )
        t.start()

    def confirm(self, enocean_id: str) -> bool:
        """Called when F6 feedback received — cancel pending timeout.
        Returns True if there was actually a pending entry (first confirmation)."""
        with self._lock:
            if enocean_id in self._pending:
                entry = self._pending.pop(enocean_id)
                logging.debug(
                    "[CONFIRM] %s acknowledged command '%s'",
                    enocean_id, entry["command"],
                )
                return True
        return False

    def _watch(self, enocean_id: str, deadline: float):
        """Background thread — fires teachin + retry if no confirmation arrives."""
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        with self._lock:
            entry = self._pending.get(enocean_id)
            if entry is None:
                return   # already confirmed
            if entry["retried"]:
                # Second timeout — give up
                self._pending.pop(enocean_id, None)
                logging.warning(
                    "[CONFIRM] %s did not respond after teachin+retry — giving up",
                    enocean_id,
                )
                return
            entry["retried"] = True
            entry["deadline"] = time.monotonic() + self.TIMEOUT_SEC

        logging.warning(
            "[CONFIRM] No F6 from %s within %.1fs — sending TeachIn + retry",
            enocean_id, self.TIMEOUT_SEC,
        )

        try:
            entry["teachin_cb"]()
            time.sleep(0.5)   # kurz warten damit Aktor TeachIn verarbeiten kann
            entry["tx_cb"]()
        except Exception as e:
            logging.error("[CONFIRM] TeachIn/retry failed: %s", e)

        # Watch for second confirmation
        t = threading.Thread(
            target=self._watch,
            args=(enocean_id, entry["deadline"]),
            daemon=True,
        )
        t.start()