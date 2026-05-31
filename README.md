# EnOcean MQTT Bridge

A Python-based bridge that connects EnOcean devices to Home Assistant via MQTT. Runs on a Raspberry Pi Zero 2W with a TCM310 USB stick (`/dev/ttyAMA0`).

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-%23FFDD00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/andreasbloch)

## Features

| Device Type | EEP | Notes |
|---|---|---|
| Light actuator | D2-01-0F | NodOn, unicast VLD, status feedback |
| Cover actuator | D2-05-00 | open / close / stop / goto position |
| Secure switch | D2-03-00 | Eltako VAES encrypted, auto teachin |
| F6 RPS dimmer | F6-02-01 | Synergie21 EOS 09, brightness control |
| Window sensor | A5-14-0A | VAES encrypted, contact + voltage |
| Switch passthrough | F6-02-01 | Physical switch → dimmer, multi-channel |

**Infrastructure**
- Home Assistant MQTT Discovery (auto-configures all entities)
- State persistence across restarts (`state/state.json`)
- RLC counter persistence for secure devices (`state/secure_state.json`)
- MQTT reconnect with exponential backoff
- Auto TeachIn for secure switches (no F6 response → teachin + retry)
- Debounce for HA brightness slider events

---

## Hardware

- Raspberry Pi Zero 2W (or any Pi)
- Enocean TCM310 module on `/dev/ttyAMA0`
- Home Assistant with MQTT broker

---

## Installation

### 1. Clone

```bash
git clone https://github.com/your-username/enocean-bridge.git
cd enocean-bridge
```

### 2. Configure credentials

```bash
cp env.example .env
nano .env
```

```env
MQTT_HOST=192.168.1.100
MQTT_PORT=1883
MQTT_USERNAME=mqtt
MQTT_PASSWORD=your_password
```

### 3. Configure devices

```bash
cp config/config.yaml.example config/config.yaml
nano config/config.yaml
```

### 4. Create state directory

```bash
mkdir -p state
```

### 5. Deploy

```bash
docker compose up -d
```

---

## Configuration

### `config/config.yaml`

#### Light (D2-01-0F)

```yaml
- name: Living_Room_Light
  enocean_id: 05AABBCC      # actuator hardware ID
  sender_id: FF956701        # gateway sub-ID (base_id + offset)
  type: light
  eep: D2-01-0F
```

#### Cover (D2-05-00)

```yaml
- name: Living_Room_Blind
  enocean_id: 05AABBCC
  sender_id: FF956702
  type: cover
  eep: D2-05-00
```

HA commands: `open`, `close`, `stop`, `0`–`100` (position)

#### Secure Switch (D2-03-00, Eltako VAES)

```yaml
- name: Bedroom_Switch
  enocean_id: 05AABBCC
  sender_id: FF956703
  type: switch
  eep: D2-03-00
  secure:
    enabled: true
    key: FA67C082F1CE65B5A93EAFCF659921B1   # 16-byte AES key
    mac_algo: 3
    rlc_algo: 2pp
```

TeachIn via MQTT: publish `teachin` to `enocean/<name>/command`

#### F6 RPS Dimmer (Synergie21 EOS 09)

```yaml
- name: Kitchen_Dimmer
  enocean_id: FF956704       # gateway sub-ID used as actuator ID
  sender_id: FF956704
  type: light
  eep: F6-02-01
  dimmer: true
  dim_on_button: 0x10        # AI = ON
  dim_off_button: 0x30       # A0 = OFF
  dim_up_button: 0x30        # A0 held = dim up
  dim_down_button: 0x10      # AI held = dim down
  dim_start_delay: 1.0       # seconds before dimming starts
  dim_full_time: 5.0         # seconds for 0% → 100%
```

#### Physical Switch → Dimmer passthrough (single channel)

```yaml
- name: Kitchen_Switch
  enocean_id: 003B1E97       # switch hardware ID
  type: switch_passthrough
  eep: F6-02-01
  linked_dimmer: Kitchen_Dimmer
  sw_up_button: 0x70         # rocker A top
  sw_down_button: 0x50       # rocker A bottom
```

#### Physical Switch → Dimmer passthrough (2-channel)

```yaml
- name: Living_Room_Switch
  enocean_id: 003B286F
  type: switch_passthrough
  eep: F6-02-01
  linked_dimmers:
    - dimmer: Living_Room_Dimmer_Front
      sw_up_button: 0x30     # rocker B top
      sw_down_button: 0x10   # rocker B bottom
    - dimmer: Living_Room_Dimmer_Back
      sw_up_button: 0x70     # rocker A top
      sw_down_button: 0x50   # rocker A bottom
```

#### Window Sensor (A5-14-0A, VAES encrypted)

```yaml
- name: Kitchen_Window
  enocean_id: 05A6BB7F
  type: sensor
  eep: A5-14-0A
  secure:
    enabled: true
    key: 9F66A5FA93FD3D15176A65FE7B5CEB09
    rlc_in_frame: true
    mac_algo: 3
```

---

## TeachIn Procedures

### D2-01-0F (NodOn light)
1. Press the button on the actuator 3× to enter learn mode
2. Send `on` via MQTT — the actuator learns the gateway sender ID

### D2-05-00 (cover)
Same as D2-01-0F

### D2-03-00 (Eltako secure switch)
1. Put actuator in secure learn mode (see actuator manual)
2. Publish `teachin` to `enocean/<name>/command`
3. The bridge sends the two RORG=0x35 frames automatically

### F6-02-01 (RPS dimmer)
1. Press the LRN button on the LED driver
2. Send `on` via MQTT within a few seconds
3. The driver learns the gateway sender ID

### switch_passthrough
The physical switch must be taught to the bridge (not the dimmer directly):
1. Put the bridge in receive mode (just running is enough — it listens for all F6)
2. Press the button on the switch — the bridge receives and stores the enocean_id
3. The switch enocean_id goes in `config.yaml` under the `switch_passthrough` entry

---

## MQTT Topics

| Topic | Direction | Payload |
|---|---|---|
| `enocean/<name>/command` | HA → Bridge | `on`, `off`, `brightness:50`, `teachin`, `open`, `close`, `stop`, `0`–`100` |
| `enocean/<name>/state` | Bridge → HA | `ON`/`OFF` or `{"state":"ON","brightness":80}` or `{"position":50}` |
| `enocean/<name>/attrs` | Bridge → HA | `{"voltage":3.2,"illumination":45,"vibration":false}` |

---

## Project Structure

```
enocean-bridge/
├── bridge/
│   ├── main.py                  # startup, discovery, init
│   ├── config.py                # YAML loader with env var substitution
│   ├── enocean_receiver.py      # RX dispatcher
│   ├── enocean_sender.py        # TX for all device types
│   ├── mqtt_client.py           # MQTT with reconnect
│   ├── state_store.py           # persistent state (state/state.json)
│   ├── confirm_tracker.py       # auto-teachin on missing feedback
│   ├── discovery_*.py           # HA MQTT discovery payloads
│   └── enocean_secure/
│       ├── crypto.py            # VAES + FHEM-CMAC (verified)
│       ├── tx.py                # secure TX + teachin (RORG=0x35)
│       ├── receiver.py          # secure RX decoder (RORG=0x31)
│       ├── context.py           # SecureContext (RLC, key)
│       └── store.py             # SecureStore → state/secure_state.json
├── config/
│   ├── config.yaml              # your device config (not committed)
│   └── config.yaml.example      # template
├── docker/
│   └── Dockerfile
├── state/                       # runtime state (not committed)
├── docker-compose.yml
├── .env                         # credentials (not committed)
├── env.example                  # template
└── .gitignore
```

---

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `MQTT_HOST` | MQTT broker hostname or IP | `192.168.1.100` |
| `MQTT_PORT` | MQTT broker port | `1883` |
| `MQTT_USERNAME` | MQTT username | `mqtt` |
| `MQTT_PASSWORD` | MQTT password | `secret` |

---

## Acknowledgements

Crypto implementation (VAES + FHEM-CMAC) verified against
[FHEM 10_EnOcean.pm](https://github.com/mhop/fhem-mirror/blob/master/fhem/FHEM/10_EnOcean.pm).
