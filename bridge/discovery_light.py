import json
import logging
import re


def publish_light_discovery(mqtt, device):
    """
    HA MQTT Discovery for light devices.
    - D2-01-0F (NodOn): on/off only, state from D2 status response
    - A5-38-08 (Dimmer): on/off + brightness 0-255, optimistic state
    """
    eep       = device.get("eep", "")
    object_id = re.sub(r"[^a-z0-9_]", "_", device["name"].lower())
    topic     = f"homeassistant/light/{object_id}/config"

    if eep == "A5-38-08" or (eep == "F6-02-01" and device.get("dimmer")):
        _publish_dimmer_discovery(mqtt, device, topic)
    else:
        _publish_onoff_discovery(mqtt, device, topic)


def _publish_onoff_discovery(mqtt, device, topic):
    """D2-01-0F NodOn — simple on/off light."""
    payload = {
        "name":          device["name"],
        "unique_id":     f"enocean_{device['enocean_id']}",
        "command_topic": f"enocean/{device['name']}/command",
        "payload_on":    "on",
        "payload_off":   "off",
        "state_topic":   f"enocean/{device['name']}/state",
        "payload_on":    "ON",
        "payload_off":   "OFF",
        "optimistic":    False,
        "device": {
            "identifiers":  [f"enocean_{device['enocean_id']}"],
            "name":         device["name"].replace("_", " "),
            "manufacturer": "NodOn",
            "model":        device.get("eep", "D2-01-0F"),
            "via_device":   "enocean_bridge",
        },
    }
    mqtt.client.publish(topic, json.dumps(payload), retain=True)
    logging.info("Published HA discovery for light %s", device["name"])


def _publish_dimmer_discovery(mqtt, device, topic):
    """A5-38-08 Dimmer — on/off + brightness control."""
    name = device["name"]
    payload = {
        "name":                    name,
        "unique_id":               f"enocean_{device['enocean_id']}",
        "command_topic":           f"enocean/{name}/command",
        "payload_on":              "on",
        "payload_off":             "off",
        "state_topic":             f"enocean/{name}/state",
        "state_value_template":    "{{ value_json.state }}",
        "brightness_command_topic": f"enocean/{name}/command",
        "brightness_command_template": "brightness:{{ value }}",
        "brightness_state_topic":  f"enocean/{name}/state",
        "brightness_value_template": "{{ value_json.brightness }}",
        "brightness_scale":        255,
        "on_command_type":         "brightness",
        "optimistic":              False,
        "device": {
            "identifiers":  [f"enocean_{device['enocean_id']}"],
            "name":         name.replace("_", " "),
            "manufacturer": "Synergie21",
            "model":        device.get("eep", "LED Driver"),
            "via_device":   "enocean_bridge",
        },
    }
    mqtt.client.publish(topic, json.dumps(payload), retain=True)
    logging.info("Published HA discovery for dimmer %s", device["name"])