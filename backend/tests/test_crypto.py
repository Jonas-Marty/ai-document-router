import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services import crypto


def test_encrypt_decrypt_round_trips() -> None:
    from cryptography.fernet import Fernet

    secret_key = Fernet.generate_key().decode()
    ciphertext = crypto.encrypt(secret_key, "super-secret-api-key")

    assert crypto.decrypt(secret_key, ciphertext) == "super-secret-api-key"
    assert ciphertext != b"super-secret-api-key"


def test_settings_reject_a_secret_key_that_is_not_a_fernet_key() -> None:
    """The deployed instance ran for half an hour with a 49-character SECRET_KEY and only
    failed when an API key was first saved, as a 500. A bad key must stop the container at
    startup instead."""
    with pytest.raises(ValidationError, match="Fernet key"):
        Settings(secret_key="not-a-fernet-key", _env_file=None)  # type: ignore[call-arg]


def test_settings_accept_a_generated_fernet_key() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()

    assert Settings(secret_key=key, _env_file=None).secret_key == key  # type: ignore[call-arg]
