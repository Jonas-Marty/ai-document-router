from cryptography.fernet import Fernet


def encrypt(secret_key: str, plaintext: str) -> bytes:
    return Fernet(secret_key.encode()).encrypt(plaintext.encode())


def decrypt(secret_key: str, ciphertext: bytes) -> str:
    return Fernet(secret_key.encode()).decrypt(ciphertext).decode()
