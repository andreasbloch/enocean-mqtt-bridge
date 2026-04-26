"""
Home Assistant MQTT Discovery for EnOcean sensors.
Currently supports:
  - A5-14-0A: Window/door contact with voltage and illumination
"""
import json
import logging


def publish_sensor_discovery(mqtt_client, device: dict):
    """Publish HA discovery payloads for a sensor device."""
    name = device["name"]
    eep  = device.get("eep", "")

    if eep == "A5-14-0A":
        _publish_window_contact(mqtt_client, name)
    else:
        logging.warning("[DISCOVERY] Unknown sensor EEP %s for %s", eep, name)


def _publish_window_contact(mqtt_client, name: str):
    """A5-14-0A: binary_sensor (contact) + voltage sensor."""
    base_topic = f"enocean/{name}"

    # binary_sensor: contact (open/closed) with JSON attributes
    config_contact = {
        "name": name,
        "unique_id": f"enocean_{name}_contact",
        "state_topic": f"{base_topic}/state",
        "payload_on": "OPEN",
        "payload_off": "CLOSED",
        "device_class": "window",
        "json_attributes_topic": f"{base_topic}/attrs",
        "device": {
            "identifiers": [f"enocean_{name}"],
            "name": name.replace("_", " "),
            "model": "A5-14-0A",
            "manufacturer": "EnOcean",
        },
    }
    mqtt_client.client.publish(
        f"homeassistant/binary_sensor/{name}/config",
        json.dumps(config_contact),
        retain=True,
    )

    # voltage as separate diagnostic sensor (same device)
    config_voltage = {
        "name": f"{name} Voltage",
        "unique_id": f"enocean_{name}_voltage",
        "state_topic": f"{base_topic}/attrs",
        "value_template": "{{ value_json.voltage }}",
        "unit_of_measurement": "V",
        "device_class": "voltage",
        "entity_category": "diagnostic",
        "device": {"identifiers": [f"enocean_{name}"]},
    }
    mqtt_client.client.publish(
        f"homeassistant/sensor/{name}_voltage/config",
        json.dumps(config_voltage),
        retain=True,
    )

    logging.info("[DISCOVERY] Published A5-14-0A discovery for %s", name)