import logging
from bridge.enocean_secure.crypto import EnOceanSecureCrypto


class EnOceanSecureTX:
    """
    Secure Control TX – Eltako D2-03-00

    Switch:   RORG=0x30, payload = [CMD, MAC0, MAC1, MAC2], broadcast
    TeachIn:  RORG=0x35, two frames (split key transfer), matches FHEM format
    RLC:      incremented + persisted after every TX
    """

    CMD_ON     = 0x0C   # B0 per EnO_switch_00Btn
    CMD_OFF    = 0x0B   # BI per EnO_switch_00Btn
    CMD_TOGGLE = 0x0C   # same as ON

    def __init__(self, context, sender_id_bytes, send_radio, secure_store=None):
        self.ctx              = context
        self.sender_id_bytes  = sender_id_bytes
        self.crypto           = EnOceanSecureCrypto(context)
        self.send_radio       = send_radio
        self.secure_store     = secure_store   # needed for RLC persistence

    # -------------------------------------------------
    # PUBLIC: Secure Switch
    # -------------------------------------------------

    def send_switch_command(self, command: str):
        if command == "on":
            cmd = self.CMD_ON
        elif command == "off":
            cmd = self.CMD_OFF
        else:
            cmd = self.CMD_TOGGLE

        self._send_secure_control(cmd)

    # -------------------------------------------------
    # PUBLIC: Secure Teach-In (RORG=0x35, split-key)
    # -------------------------------------------------

    def teach_in_sec(self):
        """
        Sends a Secure Teach-In using RORG=0x35 (SECD).
        Splits the 16-byte AES key across two frames, exactly as FHEM does.

        Frame 1 (20 data bytes):  35 | 24 | SLF | RLC(2) | key[0:10]  | SenderID | Status
        Frame 2 (13 data bytes):  35 | 40 | key[10:16]                 | SenderID | Status

        SLF=0x4B matches the value observed in FHEM teachin frames.
        RLC is read from context and NOT incremented (teachin is not a control frame).
        """
        rlc      = self.ctx.rlc_counter
        key      = self.ctx.key
        rlc_bytes = rlc.to_bytes(2, "big")
        SLF      = 0x4B

        # Frame 1: INFO(0x24) + SLF + RLC(2) + key first 10 bytes
        part1_payload = bytes([0x24, SLF]) + rlc_bytes + key[0:10]
        frame1 = (
            bytes([0x35]) +
            part1_payload +
            self.sender_id_bytes +
            bytes([0x00])
        )

        # Frame 2: 0x40 + key last 6 bytes
        part2_payload = bytes([0x40]) + key[10:16]
        frame2 = (
            bytes([0x35]) +
            part2_payload +
            self.sender_id_bytes +
            bytes([0x00])
        )

        logging.warning(
            "[SECURE] TeachInSec TX — RLC=0x%04X  SenderID=%s",
            rlc,
            self.sender_id_bytes.hex().upper(),
        )

        self.send_radio(frame1, no_optional=True)
        self.send_radio(frame2, no_optional=True)

        logging.warning(
            "[SECURE] TeachInSec sent (2 frames). "
            "Aktor MUSS sich jetzt im Anlernfenster befinden!"
        )

    # -------------------------------------------------
    # INTERNAL: Secure Control (switch command)
    # -------------------------------------------------

    def _send_secure_control(self, command_byte: int):
        rlc     = self.ctx.rlc_counter
        payload = self.crypto.build_secure_control_payload(command_byte)

        logging.info(
            "[SECURE] TX  CMD=0x%02X  RLC=0x%04X  MAC=%s  SenderID=%s",
            command_byte,
            rlc,
            payload[1:].hex().upper(),
            self.sender_id_bytes.hex().upper(),
        )

        frame = bytes([0x30]) + payload + self.sender_id_bytes + bytes([0x00])
        self.send_radio(frame, no_optional=True)

        # Increment RLC and persist immediately
        self.ctx.rlc_counter += 1
        self.ctx.mark_dirty()
        if self.secure_store:
            self.secure_store.save()
            logging.debug("[SECURE] RLC → 0x%04X  (persisted)", self.ctx.rlc_counter)
        else:
            logging.warning("[SECURE] No secure_store — RLC not persisted!")