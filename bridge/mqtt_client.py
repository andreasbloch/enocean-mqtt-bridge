import json
import logging
import paho.mqtt.client as mqtt


class MqttClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = mqtt.Client(client_id=cfg["client_id"])
        self.client.username_pw_set(cfg["username"], cfg["password"])
        self.client.on_message    = self._on_message
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.handlers      = {}
        self._state_cache  = {}
        self._subscriptions = []   # topic list — re-subscribed after reconnect

        # Auto-reconnect: wait 2s before first attempt, max 30s between retries
        self.client.reconnect_delay_set(min_delay=2, max_delay=30)

    def connect(self):
        self.client.connect(self.cfg["host"], self.cfg["port"], keepalive=60)
        # loop_forever() runs in a background thread and handles reconnects
        # automatically — unlike loop_start() it retries on disconnect.
        import threading
        t = threading.Thread(target=self.client.loop_forever, daemon=True)
        t.start()
        logging.info("MQTT connecting to %s:%s", self.cfg["host"], self.cfg["port"])

    # --------------------------------------------------
    # Callbacks
    # --------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("MQTT connected (rc=0)")
            # Re-subscribe after every (re)connect — paho does NOT do this
            # automatically when using loop_forever + reconnect_delay_set.
            for topic in self._subscriptions:
                self.client.subscribe(topic)
                logging.debug("MQTT re-subscribed to %s", topic)
            # Invalidate state cache so all states are re-published on reconnect
            self._state_cache.clear()
        else:
            logging.warning("MQTT connect failed (rc=%d)", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc == 0:
            logging.info("MQTT disconnected cleanly")
        else:
            logging.warning(
                "MQTT unexpected disconnect (rc=%d) — reconnecting automatically",
                rc,
            )

    # --------------------------------------------------
    # State Publishing
    # --------------------------------------------------

    def publish_state(self, device_name, payload):
        """
        Dispatcher:
        - dict   → JSON  (cover)
        - string → plain (switch / light / sensor)
        Skips publish if value unchanged (prevents duplicate state messages).
        """
        topic = f"enocean/{device_name}/state"

        data = json.dumps(payload) if isinstance(payload, dict) else payload

        if self._state_cache.get(topic) == data:
            return
        self._state_cache[topic] = data

        self.client.publish(topic, data, retain=False)
        logging.info("MQTT STATE %s -> %s", topic, data)

    # --------------------------------------------------
    # Subscribe
    # --------------------------------------------------

    def subscribe_commands(self, device_name, handler):
        topic = f"enocean/{device_name}/command"
        self._subscriptions.append(topic)   # remember for reconnect
        self.client.subscribe(topic)
        self.handlers[topic] = handler
        logging.info("Subscribed to %s", topic)

    def _on_message(self, client, userdata, msg):
        handler = self.handlers.get(msg.topic)
        if handler:
            handler(msg.payload.decode())