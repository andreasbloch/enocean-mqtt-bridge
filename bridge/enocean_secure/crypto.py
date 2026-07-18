import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


# VAES public key from FHEM 10_EnOcean.pm (decodeVAES)
_VAES_PUBLIC_KEY = bytes.fromhex("3410de8f1aba3eff9f5a117172eacabd")


def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    e = c.encryptor()
    return e.update(data) + e.finalize()


def _fhem_cmac(key: bytes, data_hex: str) -> bytes:
    """
    FHEM's manual single-block AES-CMAC (from EnOcean_sec_generateMAC).

    Pads data_hex with '80' then zeros to 32 hex chars (16 bytes),
    computes K1/K2 subkeys, XORs last block with K2, then AES-encrypts.
    Returns full 16-byte MAC (caller truncates to macAlgo bytes).
    """
    data_padded = bytes.fromhex((data_hex + '80').ljust(32, '0'))

    L = _aes_ecb_encrypt(key, bytes(16))
    L_int = int.from_bytes(L, 'big')

    K1_int = (L_int << 1) & ((1 << 128) - 1)
    if L_int >> 127:
        K1_int ^= 0x87

    K2_int = (K1_int << 1) & ((1 << 128) - 1)
    if K1_int >> 127:
        K2_int ^= 0x87
    K2 = K2_int.to_bytes(16, 'big')

    data_xored = bytes(a ^ b for a, b in zip(data_padded, K2))
    return _aes_ecb_encrypt(key, data_xored)


def _vaes_decrypt(key: bytes, rlc_2byte: bytes, data_16: bytes) -> bytes:
    """
    FHEM's decodeVAES: XOR-decrypts data using AES(key, PUBLIC_KEY XOR rlc).
    rlc_2byte is padded with zeros to 16 bytes (right-aligned zeros, big-endian).
    """
    rlc_padded = rlc_2byte + bytes(14)
    aes_in = bytes(a ^ b for a, b in zip(_VAES_PUBLIC_KEY, rlc_padded))
    aes_out = _aes_ecb_encrypt(key, aes_in)
    return bytes(a ^ b for a, b in zip(data_16, aes_out))


class EnOceanSecureCrypto:
    """
    Eltako D2-03-00 Secure Control Crypto — verified against FHEM 10_EnOcean.pm.

    Algorithm (from EnOcean_sec_convertToSecure for subType switch.00):
      CMD values: B0=0x0C (ON), BI=0x0B (OFF)  [from EnO_switch_00Btn]
      1. data_expanded  = CMD_byte as first byte, 15 zero bytes
      2. data_dec       = VAES_decrypt(rlc, key, data_expanded)
      3. data_end       = '0' + second_nibble_of_data_dec[0]
      4. mac_input_hex  = '30' + data_end + RLC_hex  (= 4 bytes = 8 hex chars)
      5. mac            = FHEM_CMAC(key, mac_input_hex)[:3]
      6. wire_payload   = data_end_bytes + mac  (4 bytes, no RLC in frame)
    """

    # CMD values from FHEM's %EnO_switch_00Btn
    CMD_ON     = 0x0C   # B0 (rocker B, released = ON per eventMap BI:off B0:on)
    CMD_OFF    = 0x0B   # BI (rocker B, pressed  = OFF)
    CMD_TOGGLE = 0x0C   # same as ON for toggle

    def __init__(self, context):
        self.ctx = context
        if not self.ctx.key or len(self.ctx.key) != 16:
            raise ValueError("Invalid AES key for EnOcean Secure")

    def build_secure_control_payload(self, command_byte: int) -> bytes:
        """
        Build the 4-byte SECP wire payload for a switch command.
        Returns: data_end_byte + mac_3bytes  (no RLC, no RORG)
        """
        key = self.ctx.key
        # Defensive 2-Byte-Maske: schützt vor übergelaufenen Werten,
        # falls der RLC an anderer Stelle ohne Wraparound gesetzt wurde
        rlc = self.ctx.rlc_counter & 0xFFFF
        rlc_bytes = rlc.to_bytes(2, 'big')

        # Step 1: expand CMD to 16 bytes
        data_expanded = bytes([command_byte]) + bytes(15)

        # Step 2: VAES decrypt
        data_dec = _vaes_decrypt(key, rlc_bytes, data_expanded)

        # Step 3: data_end = '0' + second nibble of first byte
        first_byte_hex = f"{data_dec[0]:02X}"
        data_end_hex = "0" + first_byte_hex[1]   # second nibble
        data_end_byte = bytes.fromhex(data_end_hex)

        # Step 4+5: MAC
        rlc_hex = f"{rlc:04X}"
        mac_input_hex = "30" + data_end_hex + rlc_hex   # 8 hex chars = 4 bytes
        mac_full = _fhem_cmac(key, mac_input_hex)
        mac_3 = mac_full[:3]

        payload = data_end_byte + mac_3

        logging.debug(
            "[SECURE-CRYPTO] CMD=0x%02X RLC=0x%04X data_end=%s MAC=%s payload=%s",
            command_byte, rlc,
            data_end_hex, mac_3.hex().upper(), payload.hex().upper(),
        )

        return payload
