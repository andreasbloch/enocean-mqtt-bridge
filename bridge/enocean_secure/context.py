class SecureContext:
    """
    Secure Context for EnOcean Secure Devices
    Hält:
    - Identität
    - Secure-Parameter
    - Rolling Code (2-Byte, wrappt automatisch bei 0xFFFF -> 0x0000)
    - Dirty-State für Persistenz
    """

    RLC_MASK = 0xFFFF  # 2-Byte RLC (rlc_algo "2pp")

    def __init__(
        self,
        enocean_id: str,
        enabled: bool,
        eep: str,
        key: bytes,
        mac_algo: int,
        rlc_algo: str,
        rlc_counter: int,
        confirm: bool = True,
    ):
        # -------------------------------------------------
        # Identität
        # -------------------------------------------------
        self.enocean_id = enocean_id.upper()
        # Byte-Repräsentation für ERP1 / Secure Control
        self.enocean_id_bytes = bytes.fromhex(self.enocean_id)
        # -------------------------------------------------
        # Secure Parameter
        # -------------------------------------------------
        self.enabled = enabled
        self.eep = eep
        self.key = key
        self.mac_algo = mac_algo
        self.rlc_algo = rlc_algo
        # geht durch den Property-Setter -> übergelaufene Werte
        # aus secure_state.json werden beim Laden automatisch maskiert
        self.rlc_counter = rlc_counter
        self.confirm = confirm
        # -------------------------------------------------
        # Persistenz-Status
        # -------------------------------------------------
        self._dirty = True

    # -------------------------------------------------
    # Rolling Code (mit Wraparound)
    # -------------------------------------------------
    @property
    def rlc_counter(self) -> int:
        return self._rlc_counter

    @rlc_counter.setter
    def rlc_counter(self, value: int):
        if value is None or not isinstance(value, int):
            raise ValueError("SecureContext requires a valid RLC counter")
        # 2-Byte Wraparound: 0xFFFF + 1 -> 0x0000 (wie FHEM / Eltako)
        self._rlc_counter = value & self.RLC_MASK

    # -------------------------------------------------
    # Lifecycle / Persistenz
    # -------------------------------------------------
    def mark_dirty(self):
        self._dirty = True

    def mark_clean(self):
        self._dirty = False

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    # -------------------------------------------------
    # Validierung
    # -------------------------------------------------
    def validate(self):
        if not self.enabled:
            return
        if not isinstance(self.enocean_id_bytes, (bytes, bytearray)):
            raise ValueError("enocean_id_bytes must be bytes")
        if len(self.enocean_id_bytes) != 4:
            raise ValueError("enocean_id must be exactly 4 bytes")
        if not self.key or len(self.key) != 16:
            raise ValueError("SecureContext requires 16-byte AES key")
        if self.rlc_counter is None or not isinstance(self.rlc_counter, int):
            raise ValueError("SecureContext requires a valid RLC counter")
