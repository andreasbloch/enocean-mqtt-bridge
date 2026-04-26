import json
import logging
import re


def publish_cover_discovery(mqtt, device):
    object_id = re.sub(r"[^a-z0-9_]", "_", device["name"].lower())

    topic = f"homeassistant/cover/{object_id}/config"

    payload = {
        "name": device["name"],
        "unique_id": f"enocean_{device['enocean_id']}",

        "command_topic": f"enocean/{device['name']}/command",
        "payload_open": "open",
        "payload_close": "close",
        "payload_stop": "stop",

        "set_position_topic": f"enocean/{device['name']}/command",
        "set_position_template": (
            "{% if position == 100 %}"
            "open"
            "{% elif position == 0 %}"
            "close"
            "{% else %}"
            "{{ 100 - position }}"
            "{% endif %}"
        ),

        "position_topic": f"enocean/{device['name']}/state",
        "position_template": "{{ 100 - value_json.position }}",

        "optimistic": False,
        "retain": False,

        "device": {
            "identifiers": [f"enocean_{device['enocean_id']}"],
            "name": device["name"],
            "manufacturer": "NodOn",
            "model": "SIN-2-RS-XX",
            "via_device": "enocean_bridge",
        },
    }

    mqtt.client.publish(topic, json.dumps(payload), retain=True)
    logging.info("Published Home Assistant discovery for %s", device["name"])