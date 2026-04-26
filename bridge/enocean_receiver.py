import json
import logging
import threading
import time

from enocean.protocol.packet import RadioPacket
from bridge.state_store import StateStore
from bridge.enocean_secure.receiver import decode_secure_4bs, decode_a5_14_0a


class EnOceanReceiver(threading.Thread):
    """
    Receives EnOcean radio packets and dispatches them to per-type handlers.
    Also exposes make_command_handler() for MQTT command subscriptions.
    """

    def __init__(self, communicator, mqtt, devices, sender, secure_store=None):
        super().__init__(daemon=True)
        self.comm         = communicator
        self.mqtt         = mqtt
        self.devices      = devices       # enocean_id → [device, ...]
        self.sender       = sender
        self.state_store  = StateStore()
        self.secure_store = secure_store
        self._press_times = {}   # enocean_id → press start time

    # --------------------------------------------------
    # Thread loop
    # --------------------------------------------------

    def run(self):
        while True:
            pkt = self.comm.receive.get()
            self._dispatch(pkt)

    # --------------------------------------------------
    # Dispatch
    # --------------------------------------------------

    def _dispatch(self, pkt):
        if not isinstance(pkt, RadioPacket):
            return

        sender_id = "".join(f"{b:02X}" for b in pkt.sender)
        for device in self.devices.get(sender_id, []):
            self._handle(pkt, device)

    def _handle(self, pkt, device):
        dtype  = device["type"]
        secure = device.get("secure", {}).get("enabled", False)

        if dtype in ("light", "switch") and pkt.rorg == 0xF6:
            if secure:
                self._on_secure_switch_feedback(pkt, device)
            else:
                self._on_unsecure_toggle(pkt, device)

        elif dtype in ("light", "switch") and not secure and pkt.rorg == 0xD2:
            # NodOn D2-01-0F sends status response after every state change
            # (both after bridge commands and after manual button presses)
            self._on_d2_01_status(pkt, device)

        elif dtype == "cover" and pkt.rorg == 0xD2:
            self._on_cover_status(pkt, device)

        elif dtype == "sensor" and pkt.rorg == 0x31:
            self._on_secure_sensor(pkt, device)

        elif dtype == "switch_passthrough" and pkt.rorg == 0xF6:
            self._on_switch_passthrough(pkt, device)

    # --------------------------------------------------
    # RX handlers
    # --------------------------------------------------

    def _on_unsecure_toggle(self, pkt, device):
        """F6 from non-secure light/switch — local button press."""
        if pkt.data[1] not in (0x10, 0x30):
            return
        prev = self.state_store.get(device["name"], "OFF")
        new  = "OFF" if prev == "ON" else "ON"
        self.state_store.set(device["name"], new)
        self.mqtt.publish_state(device["name"], new)
        logging.info("%s %s toggled locally → %s",
                     device["type"].capitalize(), device["name"], new)

    def _on_secure_switch_feedback(self, pkt, device):
        """F6 from Eltako D2-03-00 — authoritative state after switch."""
        byte = pkt.data[1]
        if byte == 0x70:
            new = "ON"
        elif byte == 0x50:
            new = "OFF"
        else:
            return

        self.state_store.set(device["name"], new)
        self.mqtt.publish_state(device["name"], new)
        logging.info("[SECURE] Switch %s F6=0x%02X → %s",
                     device["name"], byte, new)

        if not self.sender.notify_confirmed(device["enocean_id"]):
            logging.debug("[SECURE] Duplicate F6 from %s ignored",
                          device["enocean_id"])

    def _on_d2_01_status(self, pkt, device):
        """D2-01 Actuator Status Response (CMD=4) from NodOn light/switch.

        Payload layout (3 bytes, pkt.data[1:4]):
          DB2 bits[3:0] = CMD (0x4 = Status Response)
          DB1           = unused in this context
          DB0 bits[6:0] = output value (0=OFF, 1-100=ON)
          DB0 bit7      = power failure flag
        """
        payload = pkt.data[1:-5]
        if len(payload) < 3:
            return
        db2, db0 = payload[0], payload[2]
        if (db2 & 0x0F) != 0x4:
            return   # not a status response
        output_value = db0 & 0x7F
        new = "ON" if output_value > 0 else "OFF"
        self.state_store.set(device["name"], new)
        self.mqtt.publish_state(device["name"], new)
        logging.info("[D2-01] %s %s status response → %s (value=%d)",
                     device["type"].capitalize(), device["name"], new, output_value)

    def _on_switch_passthrough(self, pkt, device):
        """
        Forwards F6 events from a physical switch to linked F6 dimmer(s).
        Supports single dimmer (linked_dimmer) or multi-channel (linked_dimmers).
        """
        db0        = pkt.data[1]
        enocean_id = device["enocean_id"]

        # Build list of (dimmer_config, sw_up, sw_down) tuples
        channels = []
        if "linked_dimmers" in device:
            for ch in device["linked_dimmers"]:
                dname  = ch["dimmer"]
                sw_up  = int(ch.get("sw_up_button",   "0x70"), 16) if isinstance(ch.get("sw_up_button"),   str) else ch.get("sw_up_button",   0x70)
                sw_dn  = int(ch.get("sw_down_button", "0x50"), 16) if isinstance(ch.get("sw_down_button"), str) else ch.get("sw_down_button", 0x50)
                ddev   = self._find_device_by_name(dname)
                if ddev:
                    channels.append((ddev, dname, sw_up, sw_dn))
                else:
                    logging.warning("[PASSTHROUGH] Linked dimmer %s not found", dname)
        elif "linked_dimmer" in device:
            dname = device["linked_dimmer"]
            ddev  = self._find_device_by_name(dname)
            if ddev:
                channels.append((ddev, dname, 0x70, 0x50))
            else:
                logging.warning("[PASSTHROUGH] Linked dimmer %s not found", dname)

        if not channels:
            return

        # F6-02-01 status byte: bit2 (NU) set = real press, not set = spurious release
        # status byte is pkt.data[-1]
        status = pkt.data[-1]
        is_real_press = bool(status & 0x10)  # T21 bit: set on press, clear on spurious release

        # Dispatch to matching channel(s)
        for dimmer_dev, dimmer_name, sw_up, sw_down in channels:
            if db0 == 0x00:
                # Release (DB0=0x00) — handle pending press for this channel
                key = f"{enocean_id}_{dimmer_name}"
                press_info = self._press_times.pop(key, None)
                if press_info is None:
                    continue
                self._handle_passthrough_release(press_info, dimmer_dev, dimmer_name, enocean_id, sw_up)
            elif not is_real_press:
                # Spurious release-indicator (DB0 != 0x00 but NU bit not set)
                # This is the other rocker's release event — ignore
                logging.debug("[PASSTHROUGH] %s ignoring spurious 0x%02X (status=0x%02X)",
                              enocean_id, db0, status)
                continue
            else:
                # Real press — only handle if button belongs to this channel
                if db0 not in (sw_up, sw_down):
                    continue
                self._handle_passthrough_press(db0, enocean_id, dimmer_dev, dimmer_name, sw_up, sw_down)
        return  # skip old code below

    def _find_device_by_name(self, name):
        for dev_list in self.devices.values():
            for dev in dev_list:
                if dev["name"] == name:
                    return dev
        return None

    def _handle_passthrough_press(self, db0, enocean_id, dimmer_dev, dimmer_name, sw_up, sw_down):
        dim_start = dimmer_dev.get("dim_start_delay", 1.0)
        dim_up   = dimmer_dev.get("dim_up_button",   0x30)
        dim_down = dimmer_dev.get("dim_down_button",  0x10)
        if isinstance(dim_up,   str): dim_up   = int(dim_up,   16)
        if isinstance(dim_down, str): dim_down = int(dim_down, 16)

        press_t  = time.monotonic()
        fwd_btn  = dim_up if db0 == sw_up else dim_down
        key      = f"{enocean_id}_{dimmer_name}"
        entry    = [press_t, db0, fwd_btn, False]
        self._press_times[key] = entry

        def _delayed(eid_key, dev, btn, e):
            time.sleep(dim_start)
            if self._press_times.get(eid_key) is e:
                e[3] = True
                self.sender.send_dimmer_f6_press(dev, btn)
                logging.info("[PASSTHROUGH] %s dim started (0x%02X)", dimmer_name, btn)

        threading.Thread(target=_delayed, args=(key, dimmer_dev, fwd_btn, entry), daemon=True).start()
        logging.info("[PASSTHROUGH] %s press 0x%02X → %s (pending 0x%02X)",
                     enocean_id, db0, dimmer_name, fwd_btn)

    def _handle_passthrough_release(self, press_info, dimmer_dev, dimmer_name, enocean_id, sw_up):
        press_t, last_btn, fwd_btn, dim_started = press_info
        duration  = time.monotonic() - press_t
        dim_start = dimmer_dev.get("dim_start_delay", 1.0)
        dim_full  = dimmer_dev.get("dim_full_time",   5.0)
        dim_rate  = 100.0 / dim_full
        dim_up    = dimmer_dev.get("dim_up_button",   0x30)
        if isinstance(dim_up, str): dim_up = int(dim_up, 16)

        logging.info("[PASSTHROUGH] %s released after %.2fs", enocean_id, duration)

        try:
            currently_on    = self.state_store.get(dimmer_name + "_is_on") == "1"
            saved_brightness = int(self.state_store.get(dimmer_name + "_brightness") or 100)
        except Exception:
            currently_on = False
            saved_brightness = 100
        current = saved_brightness if currently_on else 0

        if duration < dim_start:
            dim_on  = dimmer_dev.get("dim_on_button",  0x10)
            dim_off = dimmer_dev.get("dim_off_button", 0x30)
            if isinstance(dim_on,  str): dim_on  = int(dim_on,  16)
            if isinstance(dim_off, str): dim_off = int(dim_off, 16)

            if currently_on:
                new_brightness, new_state, btn = saved_brightness, "OFF", dim_off
            else:
                new_brightness, new_state, btn = saved_brightness, "ON", dim_on

            # Update is_on immediately to prevent race on rapid presses
            self.state_store.set(dimmer_name + "_is_on", "1" if new_state == "ON" else "0")

            # Send toggle frame in background (avoids blocking receiver thread)
            def _toggle(dev=dimmer_dev, b=btn):
                self.sender.send_dimmer_f6_press(dev, b)
                time.sleep(0.1)
                self.sender.send_dimmer_f6_release(dev)
            threading.Thread(target=_toggle, daemon=True).start()

        else:
            self.sender.send_dimmer_f6_release(dimmer_dev)
            going_up   = (fwd_btn == dim_up)
            dim_dur    = duration - dim_start
            delta      = dim_dur * dim_rate
            new_brightness = current + delta if going_up else current - delta
            new_brightness = max(0, min(100, round(new_brightness)))
            new_state  = "ON" if new_brightness > 0 else "OFF"

        if new_brightness > 0:
            self.state_store.set(dimmer_name + "_brightness", str(new_brightness))
        self.state_store.set(dimmer_name + "_is_on", "1" if new_state == "ON" else "0")
        pub_brightness = new_brightness if new_state == "ON" else 0
        self.mqtt.publish_state(dimmer_name, {"state": new_state, "brightness": pub_brightness})
        logging.info("[PASSTHROUGH] %s → %s brightness=%d%%", dimmer_name, new_state, new_brightness)

    def _on_cover_status(self, pkt, device):
        """D2-05-00 position report from cover actuator."""
        pos = pkt.data[1]
        self.mqtt.publish_state(device["name"], {"position": pos})
        logging.info("Cover %s position=%d", device["name"], pos)

    def _on_secure_sensor(self, pkt, device):
        """RORG=0x31 VAES-encrypted sensor telegram."""
        sec = device.get("secure", {})
        if not sec.get("enabled") or not sec.get("key"):
            return

        payload = bytes(pkt.data[1:-5])
        inner_data, new_rlc, err = decode_secure_4bs(
            rorg_s       = 0x31,
            payload      = payload,
            key          = bytes.fromhex(sec["key"]),
            rlc_counter  = sec.get("rlc_counter", 0),
            rlc_in_frame = sec.get("rlc_in_frame", True),
        )
        if err:
            logging.warning("[SECURE-RX] %s: %s", device["name"], err)
            return

        sec["rlc_counter"] = new_rlc + 1
        if self.secure_store:
            self.secure_store.save()

        eep = device.get("eep", "")
        if eep == "A5-14-0A":
            self._publish_a5_14_0a(device, inner_data)
        else:
            logging.warning("[SECURE-RX] Unknown EEP %s for %s", eep, device["name"])

    def _publish_a5_14_0a(self, device, inner_data):
        """Decode and publish A5-14-0A window contact sensor data."""
        result = decode_a5_14_0a(inner_data)
        if result.get("teach_in"):
            logging.info("[SECURE-RX] %s teach-in telegram", device["name"])
            return

        contact = result["contact"]
        self.state_store.set(device["name"], contact)
        self.mqtt.publish_state(device["name"], contact)
        logging.info("[SECURE-RX] %s contact=%s voltage=%.2fV illum=%d vibration=%s",
                     device["name"], contact,
                     result["voltage_v"], result["illum_raw"], result["vibration"])

        self.mqtt.client.publish(
            f"enocean/{device['name']}/attrs",
            json.dumps({
                "voltage":      result["voltage_v"],
                "illumination": result["illum_raw"],
                "vibration":    result["vibration"],
            }),
            retain=True,
        )

    # --------------------------------------------------
    # MQTT command handler factory
    # --------------------------------------------------

    def make_command_handler(self, device):
        def handler(payload: str):
            command = payload.strip().lower()
            logging.info("Received command %s for device %s", payload, device["name"])

            if command == "teachin":
                self._handle_teachin(device)
            elif device["type"] in ("light", "switch"):
                self._handle_switch_command(device, command)
            elif device["type"] == "cover":
                self._handle_cover_command(device, command)

        return handler

    def _handle_teachin(self, device):
        if device.get("secure", {}).get("enabled", False):
            logging.warning("[SECURE] MQTT-triggered Teach-In for %s", device["name"])
            self.sender.send_secure_teach_in(device)
        elif device.get("eep") == "A5-38-08":
            logging.warning("[A5-38-08] MQTT-triggered TeachIn for %s", device["name"])
            self.sender.send_dimmer_teachin_a5_38_08(device)
        else:
            logging.warning("Teach-In ignored for non-secure device %s", device["name"])

    # Debounce timer per device — prevents rapid slider events from overlapping
    _debounce_timers = {}

    def _handle_switch_command(self, device, command):
        name = device["name"]
        eep  = device.get("eep", "")

        # Debounce brightness commands for F6 dimmers (slider sends many events)
        if eep == "F6-02-01" and device.get("dimmer") and command.startswith("brightness:"):
            # Cancel previous pending command
            old_timer = self._debounce_timers.pop(name, None)
            if old_timer:
                old_timer.cancel()

            # Schedule with 300ms delay
            timer = threading.Timer(
                0.3,
                self.sender.send_switch_command,
                args=(device, command),
                kwargs={"state_store": self.state_store},
            )
            timer.daemon = True
            timer.start()
            self._debounce_timers[name] = timer
            return

        self.sender.send_switch_command(device, command, state_store=self.state_store)
        # State source per EEP:
        # - D2-01-0F: D2 status response from actuator (authoritative)
        # - D2-03-00 secure: F6 response from actuator (authoritative)
        # - A5-38-08 dimmer: unidirectional — no feedback, publish optimistically
        if device.get("eep") == "A5-38-08" and command in ("on", "off"):
            self.mqtt.publish_state(device["name"], {"state": command.upper(), "brightness": 255 if command == "on" else 0})
        elif device.get("eep") == "A5-38-08" and command.startswith("brightness:"):
            pct = max(0, min(100, int(command.split(":")[1])))
            brightness = round(pct * 255 / 100)
            state = "ON" if pct > 0 else "OFF"
            self.mqtt.publish_state(device["name"], {"state": state, "brightness": brightness})

    def _handle_cover_command(self, device, command):
        if command.isdigit():
            self.sender.send_cover_goto(device, int(command))
        else:
            self.sender.send_cover_command(device, command)