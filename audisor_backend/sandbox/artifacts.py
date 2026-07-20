from hashlib import sha256


def hash_output(data: bytes) -> str:
    return sha256(data).hexdigest()

