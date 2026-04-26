import json
import logging
import re


def publish_switch_discovery(mqtt, device):
    """Home Assistant MQTT Discovery for switch devices (secure and non-secure)."""

    object_id = re.sub(r"[^a-z0-9_]", "_", device["name"].lower())
    topic     = f"homeassistant/switch/{object_id}/config"

    is_secure   = device.get("secure", {}).get("enabled", False)
    manufacturer = "Eltako" if is_secure else "NodOn"

    payload = {
        "name":           device["name"],
        "unique_id":      f"enocean_{device['enocean_id']}",
        "command_topic":  f"enocean/{device['name']}/command",
        "state_topic":    f"enocean/{device['name']}/state",
        "payload_on":     "ON",
        "payload_off":    "OFF",
        "optimistic":     False,
        "device": {
            "identifiers":  [f"enocean_{device['enocean_id']}"],
            "name":         device["name"].replace("_", " "),
            "manufacturer": manufacturer,
            "model":        device.get("eep", "Switch"),
            "via_device":   "enocean_bridge",
        },
    }

    mqtt.client.publish(topic, json.dumps(payload), retain=True)
    logging.info("Published HA discovery for switch %s", device["name"])