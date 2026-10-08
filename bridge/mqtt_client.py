import json
import logging
import threading

import paho.mqtt.client as mqtt

HA_STATUS_TOPIC = "homeassistant/status"


class _CachingClient(mqtt.Client):
    """
    paho client that records every HA discovery config published through it,
    so the discovery modules (which call mqtt.client.publish directly)
    feed the re-publish cache without any change on their side.
    """

    def __init__(self, *args, discovery_cache=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._disc_cache = discovery_cache if discovery_cache is not None else {}

    def publish(self, topic, payload=None, qos=0, retain=False, properties=None):
        if (
            isinstance(topic, str)
            and topic.startswith("homeassistant/")
            and topic.endswith("/config")
            and payload
        ):
            self._disc_cache[topic] = payload
        return super().publish(topic, payload, qos, retain)


class MqttClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._discovery_cache = {}   # topic -> payload
        self.client = _CachingClient(
            client_id=cfg["client_id"],
            discovery_cache=self._discovery_cache,
        )
        self.client.username_pw_set(cfg["username"], cfg["password"])
        self.client.on_message    = self._on_message
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.handlers          = {}
        self._state_cache      = {}   # dedup cache (cleared on every re-publish)
        self._last_state       = {}   # topic -> last published payload (never cleared)
        self._subscriptions    = []
        self._on_reconnect_cb  = None  # seeds states from state.json (startup values)
        self._seeding          = False
        self._lock             = threading.Lock()

        self.client.reconnect_delay_set(min_delay=2, max_delay=30)
        self.client.will_set("enocean/bridge/availability", "offline", retain=True)

    def connect(self):
        self.client.connect(self.cfg["host"], self.cfg["port"], keepalive=60)
        t = threading.Thread(target=self.client.loop_forever, daemon=True)
        t.start()
        logging.info("MQTT connecting to %s:%s", self.cfg["host"], self.cfg["port"])

    # --------------------------------------------------
    # Re-publish (broker reconnect, HA birth message, bridge startup)
    # --------------------------------------------------

    def _republish_everything(self, reason):
        logging.info("MQTT re-publish (%s)", reason)

        # 1) Discovery first — HA needs the config before it can use a state
        for topic, payload in list(self._discovery_cache.items()):
            self.client.publish(topic, payload, retain=True)
        logging.info("MQTT re-published %d discovery configs", len(self._discovery_cache))

        # 2) Availability
        self.client.publish("enocean/bridge/availability", "online", retain=True)

        # 3) States
        with self._lock:
            snapshot = dict(self._last_state)
            self._state_cache.clear()

        # 3a) Seed from state.json — only for topics never published since start
        if self._on_reconnect_cb:
            self._seeding = True
            try:
                self._on_reconnect_cb()
            finally:
                self._seeding = False

        # 3b) Live states (fresher than state.json)
        for topic, data in snapshot.items():
            self.client.publish(topic, data, retain=True)
            self._state_cache[topic] = data
        logging.info("MQTT re-published %d live states", len(snapshot))

    # --------------------------------------------------
    # Callbacks
    # --------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            logging.warning("MQTT connect failed (rc=%d)", rc)
            return

        logging.info("MQTT connected (rc=0)")

        self.client.subscribe(HA_STATUS_TOPIC)
        for topic in self._subscriptions:
            self.client.subscribe(topic)
            logging.debug("MQTT re-subscribed to %s", topic)

        self._republish_everything("broker (re)connect")

    def _on_disconnect(self, client, userdata, rc):
        if rc == 0:
            logging.info("MQTT disconnected cleanly")
        else:
            logging.warning(
                "MQTT unexpected disconnect (rc=%d) — reconnecting automatically",
                rc,
            )
        # No publish here: connection is gone, Last Will handles "offline".

    # --------------------------------------------------
    # Discovery Publishing (explicit API, optional now)
    # --------------------------------------------------

    def publish_discovery(self, topic, payload):
        data = json.dumps(payload) if isinstance(payload, dict) else payload
        self.client.publish(topic, data, retain=True)   # cached by _CachingClient
        logging.info("MQTT DISCOVERY %s", topic)

    # --------------------------------------------------
    # State Publishing
    # --------------------------------------------------

    def publish_state(self, device_name, payload):
        """
        dict   -> JSON  (cover, dimmer)
        string -> plain (switch / light / sensor)
        Skips publish if value unchanged. Retained.
        """
        topic = f"enocean/{device_name}/state"
        data = json.dumps(payload) if isinstance(payload, dict) else payload

        with self._lock:
            # During seeding from state.json: never override a fresher live value
            if self._seeding and topic in self._last_state:
                return
            if self._state_cache.get(topic) == data:
                return
            self._state_cache[topic] = data
            self._last_state[topic] = data

        self.client.publish(topic, data, retain=True)
        logging.info("MQTT STATE %s -> %s", topic, data)

    # --------------------------------------------------
    # Subscribe
    # --------------------------------------------------

    def subscribe_commands(self, device_name, handler):
        topic = f"enocean/{device_name}/command"
        self._subscriptions.append(topic)
        self.client.subscribe(topic)
        self.handlers[topic] = handler
        logging.info("Subscribed to %s", topic)

    def _on_message(self, client, userdata, msg):
        if msg.topic == HA_STATUS_TOPIC:
            if msg.payload.decode() == "online":
                # Own thread so the network loop is not blocked
                threading.Thread(
                    target=self._republish_everything,
                    args=("HA birth message",),
                    daemon=True,
                ).start()
            return

        handler = self.handlers.get(msg.topic)
        if handler:
            handler(msg.payload.decode())