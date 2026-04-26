import logging


class EnOceanSecureTeachIn:
    """
    Secure Teach-In Handling (Phase 3.5)

    Wird aufgerufen, wenn Secure-Kommunikation
    aufgrund veralteter RLCs scheitert.
    """

    def __init__(self, ctx, send_radio):
        self.ctx = ctx
        self.send_radio = send_radio

    def perform(self):
        """
        Sendet Teach-In-Secure Telegramm.
        Für Eltako D2-03-00 ausreichend.
        """
        logging.warning(
            "[SECURE] Performing TeachInSec for %s",
            self.ctx.enocean_id
        )

        # Secure Teach-In: D2-03-00 FUNC=0, TYPE=0
        payload = bytes([
            0xD2, 0x03, 0x00, 0x00
        ])

        # Teacher-ID ist egal, da verschlüsselt
        self.send_radio(payload)