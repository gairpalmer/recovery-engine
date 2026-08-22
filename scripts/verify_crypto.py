"""Prove the dashboard AES-GCM payload round-trips and rejects a wrong passphrase.
Confirms the Python encryption is sound; the browser uses matching WebCrypto params.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from dashboard import _encrypt

d = base64.b64decode
msg = "hello recovery <b>secret</b> 66/100"
pw = "test-pass-123"
blob = _encrypt(msg, pw)


def _key(passphrase):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=d(blob["salt"]),
                      iterations=blob["iter"]).derive(passphrase.encode())


pt = AESGCM(_key(pw)).decrypt(d(blob["iv"]), d(blob["ct"]), None).decode()
assert pt == msg, "roundtrip mismatch"

try:
    AESGCM(_key("wrong")).decrypt(d(blob["iv"]), d(blob["ct"]), None)
    print("FAIL: wrong passphrase was accepted")
except Exception:
    print(f"OK: roundtrip works, ciphertext {len(blob['ct'])}b64 chars, wrong passphrase rejected")
