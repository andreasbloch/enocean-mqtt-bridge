import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

_PUBLIC_KEY = bytes.fromhex("3410de8f1aba3eff9f5a117172eacabd")

# 2-Byte Rolling Code (rlc_algo "2pp" / A5-14-0A)
_RLC_MASK = 0xFFFF

# Forward acceptance window for replay protection (per FHEM spec: 128)
REPLAY_WINDOW = 128


def _aes_ecb(key: bytes, data: bytes) -> bytes:
    c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    e = c.encryptor()
    return e.update(data) + e.finalize()


def _vaes_decrypt(key: bytes, rlc_2byte: bytes, data: bytes) -> bytes:
    """VAES decrypt — symmetric, same operation for encrypt and decrypt."""
    rlc_padded = rlc_2byte + bytes(14)
    aes_in = bytes(a ^ b for a, b in zip(_PUBLIC_KEY, rlc_padded))
    aes_out = _aes_ecb(key, aes_in)
    data_padded = (data + bytes(16))[:16]
    return bytes(a ^ b for a, b in zip(data_padded, aes_out))


def _fhem_cmac(key: bytes, data_hex: str) -> bytes:
    """FHEM's single-block AES-CMAC."""
    data_padded = bytes.fromhex((data_hex + '80').ljust(32, '0'))
    L = _aes_ecb(key, bytes(16))
    L_int = int.from_bytes(L, 'big')
    K1_int = (L_int << 1) & ((1 << 128) - 1)
    if L_int >> 127: K1_int ^= 0x87
    K2_int = (K1_int << 1) & ((1 << 128) - 1)
    if K1_int >> 127: K2_int ^= 0x87
    K2 = K2_int.to_bytes(16, 'big')
    return _aes_ecb(key, bytes(a ^ b for a, b in zip(data_padded, K2)))


def decode_secure_4bs(
    rorg_s: int,
    payload: bytes,
    key: bytes,
    rlc_counter: int,
    rlc_in_frame: bool = True,
    mac_len: int = 3,
    replay_window: int | None = REPLAY_WINDOW,
) -> tuple[bytes | None, int | None, str | None]:
    """
    Decode an incoming RORG=0x31 (Secure with encapsulation) telegram.

    Wire layout (rlcTX=true, macAlgo=3):
        [encrypted_data] [RLC 2 bytes] [MAC 3 bytes]

    Replay protection (rlc_in_frame=True):
        The RLC transmitted in the frame is only accepted if it lies in the
        wrap-aware FORWARD window relative to the stored counter:
            delta = (frame_rlc - rlc_counter) & 0xFFFF
            accept if delta < replay_window
        Pass replay_window=None to disable the check (initial sync after
        restart — the caller adopts the frame RLC as new baseline).

    Returns:
        (inner_data_bytes, new_rlc, error_str)
        inner_data_bytes is the decrypted 4BS payload (4 bytes, without RORG)
        On failure returns (None, None, error_message)
    """
    payload_hex = payload.hex().upper()
    total = len(payload_hex)

    if rlc_in_frame:
        rlc_chars = 4   # 2 bytes
        mac_chars = mac_len * 2
        if total < rlc_chars + mac_chars + 2:
            return None, None, "Payload too short"
        data_enc_hex = payload_hex[:total - rlc_chars - mac_chars]
        rlc_hex      = payload_hex[len(data_enc_hex) : len(data_enc_hex) + rlc_chars]
        mac_rx_hex   = payload_hex[len(data_enc_hex) + rlc_chars:]
        rlc_val = int(rlc_hex, 16)

        # --- Replay protection: wrap-aware forward window ---
        if replay_window is not None:
            stored = rlc_counter & _RLC_MASK
            delta = (rlc_val - stored) & _RLC_MASK
            if delta >= replay_window:
                return None, None, (
                    f"Replay/out-of-window: frame RLC=0x{rlc_val:04X}, "
                    f"stored=0x{stored:04X}, delta={delta}, "
                    f"window={replay_window}"
                )
    else:
        mac_chars = mac_len * 2
        data_enc_hex = payload_hex[:total - mac_chars]
        mac_rx_hex   = payload_hex[total - mac_chars:]
        rlc_val = rlc_counter & _RLC_MASK

    # Search window of 128 (per FHEM spec)
    search_start = rlc_val if rlc_in_frame else (rlc_counter & _RLC_MASK)
    for offset in range(128 if not rlc_in_frame else 1):
        rlc_try = (search_start + offset) & _RLC_MASK
        rlc_try_hex = f"{rlc_try:04X}"

        # Verify MAC
        rorg_hex = f"{rorg_s:02X}"
        mac_expected = _fhem_cmac(key, rorg_hex + data_enc_hex + rlc_try_hex)[:mac_len]

        if mac_expected.hex().upper() == mac_rx_hex.upper():
            # MAC verified — decrypt
            rlc_bytes = bytes.fromhex(rlc_try_hex)
            data_enc = bytes.fromhex(data_enc_hex)
            data_dec = _vaes_decrypt(key, rlc_bytes, data_enc)
            data_dec_hex = data_dec.hex().upper()

            # First 2 hex chars = inner RORG, rest = inner data
            inner_rorg_hex = data_dec_hex[:2]
            inner_data_hex = data_dec_hex[2:2 + len(data_enc_hex) - 2]
            inner_data = bytes.fromhex(inner_data_hex)

            logging.debug(
                "[SECURE-RX] Verified RLC=0x%04X inner_rorg=0x%s data=%s",
                rlc_try, inner_rorg_hex, inner_data_hex,
            )
            return inner_data, rlc_try, None

    return None, None, f"MAC verification failed (rlc=0x{rlc_val:04X}, window=128)"


def decode_a5_14_0a(inner_data: bytes) -> dict:
    """
    Decode A5-14-0A (Window Contact with Supply Voltage and Illumination).

    DB3 = supply voltage (0–255 → 0–5.1V)
    DB2 = illumination  (0–255, raw)
    DB1 bit1 = vibration (1 = vibration detected)
    DB1 bit0 = contact   (0 = open / magnet present, 1 = closed / magnet away)
    DB0 bit3 = LRN       (1 = data telegram, 0 = teach-in)
    """
    if len(inner_data) < 4:
        return {"error": "payload too short"}

    db3, db2, db1, db0 = inner_data[0], inner_data[1], inner_data[2], inner_data[3]

    lrn = bool(db0 & 0x08)
    if not lrn:
        return {"teach_in": True}

    voltage   = round(db3 * 5.1 / 255, 2)
    illum_raw = db2
    vibration = bool(db1 & 0x02)
    contact   = bool(db1 & 0x01)   # True = magnet away = window OPEN

    return {
        "contact":   "OPEN" if contact else "CLOSED",
        "voltage_v": voltage,
        "illum_raw": illum_raw,
        "vibration": vibration,
        "lrn":       lrn,
    }
