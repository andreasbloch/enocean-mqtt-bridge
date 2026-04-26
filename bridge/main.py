import logging

from enocean.communicators.serialcommunicator import SerialCommunicator

from bridge.config import load_config
from bridge.mqtt_client import MqttClient
from bridge.enocean_sender import EnOceanSender
from bridge.enocean_receiver import EnOceanReceiver
from bridge.enocean_secure.store import SecureStore
from bridge.enocean_secure.context import SecureContext

from bridge.discovery_light import publish_light_discovery
from bridge.discovery_cover import publish_cover_discovery
from bridge.discovery_sensor import publish_sensor_discovery
from bridge.discovery_switch import publish_switch_discovery


logging.basicConfig(level=logging.INFO)


def main():
    # -------------------------------------------------
    # Config & MQTT
    # -------------------------------------------------
    cfg = load_config("/app/config/config.yaml")

    mqtt = MqttClient(cfg["mqtt"])
    mqtt.connect()

    # -------------------------------------------------
    # Secure Store
    # -------------------------------------------------
    secure_store = SecureStore()

    # ✅ WICHTIG: Secure-Contexts aus Config registrieren
    for device_list in cfg["devices_by_id"].values():
        for device in device_list:
            sec = device.get("secure")
            if not sec or not sec.get("enabled"):
                continue

            enocean_id = device["enocean_id"].upper()  # ✅ Normalisieren!

            # Use persisted RLC from secure_state.json if available.
            # config.yaml rlc_counter is intentionally ignored — it is always
            # stale. The auto-teachin mechanism handles first-use sync.
            existing = secure_store.get(enocean_id)
            if existing:
                rlc = existing.rlc_counter
                logging.info(
                    "[SECURE] Using persisted RLC=0x%04X for %s",
                    rlc, device["name"],
                )
            else:
                rlc = 0x0000   # fresh start — auto-teachin will sync on first TX

            ctx = SecureContext(
                enocean_id=enocean_id,
                enabled=True,
                eep=device.get("eep"),
                key=bytes.fromhex(sec["key"]),
                mac_algo=sec.get("mac_algo"),
                rlc_algo=sec.get("rlc_algo"),
                rlc_counter=rlc,
                confirm=sec.get("confirm", True),
            )

            ctx.validate()
            secure_store.register(ctx)

            logging.info(
                "[SECURE] Registered secure context for %s",
                device["name"],
            )

    # -------------------------------------------------
    # Serial / ESP3
    # -------------------------------------------------
    communicator = SerialCommunicator(
        port=cfg["enocean"]["port"]
    )
    communicator.start()

    # -------------------------------------------------
    # Sender (TX)
    # -------------------------------------------------
    sender = EnOceanSender(
        communicator=communicator,
        secure_store=secure_store
    )
    # Wire MQTT publish callback for unidirectional dimmers (F6, A5-38-08)
    sender._mqtt_publish = mqtt.publish_state

    # -------------------------------------------------
    # Discovery
    # -------------------------------------------------
    for device_list in cfg["devices_by_id"].values():
        for device in device_list:
            if device["type"] == "light":
                publish_light_discovery(mqtt, device)
            elif device["type"] == "cover":
                publish_cover_discovery(mqtt, device)
            elif device["type"] == "sensor":
                publish_sensor_discovery(mqtt, device)
            elif device["type"] == "switch":
                publish_switch_discovery(mqtt, device)

    # -------------------------------------------------
    # Receiver (RX + MQTT handler)
    # -------------------------------------------------
    receiver = EnOceanReceiver(
        communicator=communicator,
        mqtt=mqtt,
        devices=cfg["devices_by_id"],
        sender=sender,
        secure_store=secure_store,
    )
    receiver.start()

    # -------------------------------------------------
    # MQTT Command Subscriptions
    # -------------------------------------------------
    for device_list in cfg["devices_by_id"].values():
        for device in device_list:
            mqtt.subscribe_commands(
                device["name"],
                receiver.make_command_handler(device),
            )

    logging.info("EnOcean bridge started")

    receiver.join()


if __name__ == "__main__":
    main()