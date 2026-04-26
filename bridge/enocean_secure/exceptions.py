class SecureError(Exception):
    pass


class SecureNotInitialized(SecureError):
    pass


class SecureConfirmTimeout(SecureError):
    pass