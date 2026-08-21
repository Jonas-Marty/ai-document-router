from app.services import crypto


def test_encrypt_decrypt_round_trips() -> None:
    from cryptography.fernet import Fernet

    secret_key = Fernet.generate_key().decode()
    ciphertext = crypto.encrypt(secret_key, "super-secret-api-key")

    assert crypto.decrypt(secret_key, ciphertext) == "super-secret-api-key"
    assert ciphertext != b"super-secret-api-key"
