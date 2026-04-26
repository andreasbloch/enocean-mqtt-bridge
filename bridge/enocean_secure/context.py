class SecureContext:
    """
    Secure Context for EnOcean Secure Devices

    Hält:
    - Identität
    - Secure-Parameter
    - Rolling Code
    - Dirty-State für Persistenz
    """

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
        self.rlc_counter = rlc_counter
        self.confirm = confirm

        # -------------------------------------------------
        # Persistenz-Status
        # -------------------------------------------------
        self._dirty = True

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