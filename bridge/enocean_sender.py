import logging
import time
import threading
from enocean.protocol.packet import Packet
from enocean.protocol.constants import PACKET

from bridge.enocean_secure.tx import EnOceanSecureTX
from bridge.confirm_tracker import ConfirmTracker


class EnOceanSender:
    """
    EnOcean TX — handles lights (D2-01, F6), covers (D2-05) and
    secure switches (D2-03-00 VAES).
    """

    def __init__(self, communicator, secure_store):
        self.comm                = communicator
        self.secure_store        = secure_store
        self._secure_tx          = {}   # enocean_id → EnOceanSecureTX
        self.confirm_tracker     = ConfirmTracker()

    # --------------------------------------------------
    # Public — switch / light
    # --------------------------------------------------

    def send_switch_command(self, device: dict, command: str, state_store=None):
        command = command.lower()
        eep = device.get("eep", "")
        if device.get("secure", {}).get("enabled"):
            self._secure_switch(device, command)
        elif eep == "A5-38-08":
            self.send_dimmer_a5_38_08(device, command)
        elif eep == "F6-02-01" and device.get("dimmer"):
            self.send_dimmer_f6(device, command, state_store=state_store)
        elif eep.startswith("D2-01"):
            self._light_d2_01(device, command)
        else:
            self._light_f6_toggle(device, command)

    # --------------------------------------------------
    # Public — cover
    # --------------------------------------------------

    def send_cover_command(self, device: dict, command: str):
        sid = bytes.fromhex(device["sender_id"])
        eid = bytes.fromhex(device["enocean_id"])
        if command == "open":
            self._send_radio(bytes([0xD2, 0x00, 0x7F, 0x00, 0xF1]) + sid + b"\x00", dest_id=eid)
        elif command == "close":
            self._send_radio(bytes([0xD2, 0x64, 0x7F, 0x00, 0xF1]) + sid + b"\x00", dest_id=eid)
        elif command == "stop":
            # D2-05-00 Stop = 1-byte payload (confirmed from FHEM)
            self._send_radio(bytes([0xD2, 0xF2]) + sid + b"\x00", dest_id=eid)

    def send_cover_goto(self, device: dict, position: int):
        sid = bytes.fromhex(device["sender_id"])
        eid = bytes.fromhex(device["enocean_id"])
        pos = max(0, min(100, int(position)))
        self._send_radio(bytes([0xD2, pos, 0x7F, 0x00, 0xF1]) + sid + b"\x00", dest_id=eid)

    # --------------------------------------------------
    # Public — secure
    # --------------------------------------------------

    def send_secure_teach_in(self, device: dict):
        tx = self._get_tx(device)
        if tx:
            tx.teach_in_sec()

    def notify_confirmed(self, enocean_id: str) -> bool:
        """F6 feedback received — cancel pending auto-teachin timer."""
        return self.confirm_tracker.confirm(enocean_id)

    # --------------------------------------------------
    # Private — light
    # --------------------------------------------------

    def _light_d2_01(self, device: dict, command: str):
        """NodOn D2-01-0F: 3-byte VLD, unicast."""
        if command not in ("on", "off"):
            return
        sid = bytes.fromhex(device["sender_id"])
        eid = bytes.fromhex(device["enocean_id"])
        val = 0x64 if command == "on" else 0x00
        self._send_radio(bytes([0xD2, 0x01, 0x00, val]) + sid + b"\x00", dest_id=eid)
        logging.info("[D2-01] Light %s → %s (unicast to %s)",
                     device["name"], command.upper(), device["enocean_id"])

    def _light_f6_toggle(self, device: dict, command: str):
        """F6-02-01 rocker — toggle only (no true on/off)."""
        sid = bytes.fromhex(device["sender_id"])
        self._send_radio(bytes([0x10]) + sid + b"\x00")
        time.sleep(0.05)
        self._send_radio(bytes([0x00]) + sid + b"\x00")
        logging.info("[F6] Light %s toggled", device["name"])

    # --------------------------------------------------
    # Private — secure switch
    # --------------------------------------------------

    def _secure_switch(self, device: dict, command: str):
        tx = self._get_tx(device)
        if not tx:
            return
        tx.send_switch_command(command)
        eid = device["enocean_id"]
        self.confirm_tracker.expect(
            enocean_id = eid,
            command    = command,
            tx_cb      = lambda: tx.send_switch_command(command),
            teachin_cb = lambda: tx.teach_in_sec(),
        )

    def _get_tx(self, device: dict) -> EnOceanSecureTX | None:
        """Return cached EnOceanSecureTX, creating it on first call."""
        eid = device["enocean_id"]
        if eid not in self._secure_tx:
            ctx = self.secure_store.get(eid)
            if not ctx:
                logging.error("[SECURE] No secure context for %s", eid)
                return None
            self._secure_tx[eid] = EnOceanSecureTX(
                context        = ctx,
                sender_id_bytes= bytes.fromhex(device["sender_id"]),
                send_radio     = self._send_radio,
                secure_store   = self.secure_store,
            )
        return self._secure_tx[eid]

    # --------------------------------------------------
    # Private — ESP3 send
    # --------------------------------------------------

    def _send_radio(self, payload: bytes, dest_id: bytes = None, no_optional: bool = False):
        """
        Send ESP3 RADIO_ERP1.
        dest_id:     unicast destination (4 bytes). None = broadcast.
        no_optional: True for SECP/SECD frames (opt_len=0, matches FHEM).
        """
        if no_optional:
            optional = []
        elif dest_id is not None and len(dest_id) == 4:
            optional = [0x03] + list(dest_id) + [0xFF, 0x00]
        else:
            optional = [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]

        pkt = Packet(packet_type=PACKET.RADIO_ERP1, data=list(payload), optional=optional)
        logging.warning("[ESP3-TX] %s", " ".join(f"{b:02X}" for b in pkt.build()))
        self.comm.send(pkt)

    # --------------------------------------------------
    # A5-38-08 Dimmer
    # --------------------------------------------------

    def send_dimmer_a5_38_08(self, device: dict, command: str):
        """
        A5-38-08 Gateway / Dimmer — 4BS telegram, broadcast.

        Commands:
          on            → CMD=0x02, brightness=255, switch=1
          off           → CMD=0x02, brightness=0,   switch=0
          brightness:N  → CMD=0x02, brightness=N (0-255), switch=1
        """
        sid = bytes.fromhex(device["sender_id"])

        if command == "on":
            db2, db0 = 0xFF, 0x09   # 100%, LRN=1, switch=1
        elif command == "off":
            db2, db0 = 0x00, 0x08   # 0%,   LRN=1, switch=0
        elif command.startswith("brightness:"):
            pct = max(0, min(100, int(command.split(":")[1])))
            db2 = round(pct * 255 / 100)
            db0 = 0x09 if pct > 0 else 0x08
        else:
            logging.warning("[A5-38-08] Unknown command: %s", command)
            return

        # CMD=0x02 (dim), speed=1s ramp
        self._send_radio(bytes([0xA5, 0x02, db2, 0x01, db0]) + sid + b"\x00")
        logging.info("[A5-38-08] Dimmer %s → %s (DB2=0x%02X)",
                     device["name"], command, db2)

    def send_dimmer_teachin_a5_38_08(self, device: dict):
        """
        Send A5-38-08 4BS TeachIn telegram (broadcast).
        Actuator must be in learn mode when this is sent.
        EEP A5-38-08: func=0x38, type=0x08, manufID=0x7FF
        """
        sid = bytes.fromhex(device["sender_id"])
        # DB3/DB2/DB1 encode EEP + ManufID per 4BS teachin spec
        # DB0 bit3=LRN=0 (teachin telegram)
        self._send_radio(bytes([0xA5, 0xE1, 0x07, 0xFF, 0x00]) + sid + b"\x00")
        logging.warning("[A5-38-08] TeachIn sent for %s (sender=%s) — "
                        "actuator MUST be in learn mode!",
                        device["name"], device["sender_id"])

    # --------------------------------------------------
    # F6 RPS Dimmer (Synergie21 EOS 09)
    # --------------------------------------------------

    # Timing constants (from hardware measurement):
    _DIM_START_DELAY = 1.0   # seconds before dimming starts after press
    _DIM_FULL_TIME   = 5.0   # seconds to go from 0% to 100% after start
    # → dim rate = 20% per second after start delay

    def send_dimmer_f6(self, device: dict, command: str, state_store=None):
        """
        F6 RPS dimmer (Synergie21 EOS 09).

        State tracking uses two separate keys:
          name_brightness: last known brightness level (0-100, preserved on off)
          name_is_on:      boolean whether treiber is currently on

        on/off:     dim_on_button / dim_off_button (short press < 1s)
        brightness: dim_up_button / dim_down_button (long press ≥ 1s)
        from zero:  dim_up_button held from 0 to target
        """
        sid  = bytes.fromhex(device["sender_id"])
        name = device["name"]

        btn_on   = device.get("dim_on_button",   0x10)
        btn_off  = device.get("dim_off_button",  0x30)
        btn_up   = device.get("dim_up_button",   0x30)
        btn_down = device.get("dim_down_button", 0x10)
        btn_on   = int(btn_on,   16) if isinstance(btn_on,   str) else btn_on
        btn_off  = int(btn_off,  16) if isinstance(btn_off,  str) else btn_off
        btn_up   = int(btn_up,   16) if isinstance(btn_up,   str) else btn_up
        btn_down = int(btn_down, 16) if isinstance(btn_down, str) else btn_down

        # Read current state — brightness is preserved across off
        brightness = 100
        is_on      = False
        if state_store:
            try:
                b = state_store.get(name + "_brightness")
                brightness = int(b) if b else 100
            except (ValueError, TypeError):
                brightness = 100
            try:
                o = state_store.get(name + "_is_on")
                is_on = (o == "1")
            except (ValueError, TypeError):
                is_on = False

        current = brightness if is_on else 0

        dim_start = device.get("dim_start_delay", self._DIM_START_DELAY)
        dim_full  = device.get("dim_full_time",   self._DIM_FULL_TIME)
        dim_rate  = 100.0 / dim_full

        def press(btn):
            self._send_radio(bytes([0xF6, btn]) + sid + bytes([0x30]), no_optional=True)

        def release():
            self._send_radio(bytes([0xF6, 0x00]) + sid + bytes([0x20]), no_optional=True)

        def _run(btn, hold, new_brightness, new_is_on):
            press(btn)
            time.sleep(hold)
            release()
            if state_store:
                if new_brightness > 0:
                    state_store.set(name + "_brightness", str(new_brightness))
                state_store.set(name + "_is_on", "1" if new_is_on else "0")
            new_state = "ON" if new_is_on else "OFF"
            pub_brightness = new_brightness if new_is_on else 0
            if self._mqtt_publish:
                self._mqtt_publish(name, {"state": new_state, "brightness": pub_brightness})
            logging.info("[F6-DIM] %s → %s brightness=%d%%", name, new_state, new_brightness)

        def go(btn, hold, new_brightness, new_is_on):
            threading.Thread(target=_run, args=(btn, hold, new_brightness, new_is_on), daemon=True).start()

        if command == "on":
            # Short press ON — treiber restores its last brightness
            go(btn_on, 0.1, brightness, True)

        elif command == "off":
            # Short press OFF — preserve brightness for next on
            go(btn_off, 0.1, brightness, False)

        elif command.startswith("brightness:"):
            target = max(0, min(100, int(command.split(":")[1])))

            if target == 0:
                go(btn_off, 0.1, brightness, False)
                return

            if not is_on:
                # Treiber is OFF — dim up from zero to target
                hold = dim_start + (target / dim_rate)
                logging.info("[F6-DIM] %s OFF→%d%% (from zero) hold=%.2fs",
                             name, target, hold)
                go(btn_up, hold, target, True)
            else:
                delta = abs(target - current)
                if delta < 2:
                    logging.debug("[F6-DIM] %s already at ~%d%%", name, current)
                    return
                going_up = target > current
                btn  = btn_up if going_up else btn_down
                hold = dim_start + (delta / dim_rate)
                logging.info("[F6-DIM] %s %s %d%%→%d%% hold=%.2fs",
                             name, "up" if going_up else "down", current, target, hold)
                go(btn, hold, target, True)

        else:
            logging.warning("[F6-DIM] Unknown command: %s", command)

    def send_dimmer_f6_press(self, device: dict, btn: int):
        """Send a single F6 press frame (for switch passthrough)."""
        sid = bytes.fromhex(device["sender_id"])
        self._send_radio(bytes([0xF6, btn]) + sid + bytes([0x30]), no_optional=True)

    def send_dimmer_f6_release(self, device: dict):
        """Send a single F6 release frame (for switch passthrough)."""
        sid = bytes.fromhex(device["sender_id"])
        self._send_radio(bytes([0xF6, 0x00]) + sid + bytes([0x20]), no_optional=True)

    _mqtt_publish = None