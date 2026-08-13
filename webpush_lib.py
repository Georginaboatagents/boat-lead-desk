"""Minimal Web Push sender (RFC 8291 aes128gcm + RFC 8292 VAPID).
Uses only the preinstalled `cryptography` package — no pip installs needed.
"""
import base64, json, os, struct, time, urllib.request, urllib.error
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64u_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(plaintext, ua_public_b64, auth_b64, as_private=None, salt=None):
    """RFC 8291 aes128gcm encryption. as_private/salt injectable for tests."""
    ua_public = b64u_decode(ua_public_b64)
    auth = b64u_decode(auth_b64)
    if as_private is None:
        as_private = ec.generate_private_key(ec.SECP256R1())
    if salt is None:
        salt = os.urandom(16)
    as_public = as_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ua_pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_pub_key)
    ikm = hkdf(auth, ecdh, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = plaintext + b"\x02"  # last-record delimiter
    ct = AESGCM(cek).encrypt(nonce, padded, None)
    header = salt + struct.pack("!I", 4096) + bytes([len(as_public)]) + as_public
    return header + ct


def vapid_headers(endpoint, vapid_private_b64, sub_email):
    from urllib.parse import urlsplit
    u = urlsplit(endpoint)
    aud = u.scheme + "://" + u.netloc
    priv = ec.derive_private_key(int.from_bytes(b64u_decode(vapid_private_b64), "big"), ec.SECP256R1())
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600,
                              "sub": "mailto:" + sub_email}).encode())
    signing = (head + "." + claims).encode()
    der = priv.sign(signing, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    jwt = head + "." + claims + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": "vapid t=" + jwt + ",k=" + b64u(pub)}


def send_push(subscription, payload_dict, vapid_private_b64, sub_email, ttl=3600):
    """Returns (status_code, reason). 201/200 = delivered to push service.
    404/410 = subscription dead — prune it. Raises on network failure."""
    endpoint = subscription["endpoint"]
    body = encrypt_payload(json.dumps(payload_dict).encode(),
                           subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    headers = vapid_headers(endpoint, vapid_private_b64, sub_email)
    headers.update({"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                    "TTL": str(ttl), "Urgency": "high"})
    req = urllib.request.Request(endpoint, method="POST", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, "ok"
    except urllib.error.HTTPError as e:
        return e.code, e.reason


if __name__ == "__main__":
    # Self-test against RFC 8291 Appendix A test vector
    as_priv_raw = b64u_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")
    as_private = ec.derive_private_key(int.from_bytes(as_priv_raw, "big"), ec.SECP256R1())
    out = encrypt_payload(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        as_private=as_private,
        salt=b64u_decode("DGv6ra1nlYgDCS1FRnbzlw"))
    expected = b64u_decode(
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e"
        "3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qu"
        "lcy4a-fN")
    print("RFC 8291 test vector:", "PASS" if out == expected else "FAIL")
    print("out :", b64u(out))
