"""aiortc SRTP key override for TP-Link's WebRTC media protocol.

TP-Link's cameras run a normal DTLS handshake for transport/ICE purposes,
but then ignore the SRTP keying material DTLS would normally export -
instead they expect an HKDF-SHA256-derived key (from the camera's nonce +
salt + the account password) to be used for the actual SRTP session. No
WebRTC stack exposes a public API for "use this key instead of the DTLS
exporter's" (SRTP keying is normally derived automatically per RFC 5764),
so this replaces the one small private method involved
(``RTCDtlsTransport._setup_srtp``) rather than forking aiortc wholesale.

Confirmed byte layout: the 60-byte HKDF output is exactly
2 x (16-byte key + 14-byte salt) - the standard bidirectional keying
material layout for the SRTP_AES128_CM_SHA1_80 profile
(``aiortc.rtcdtlstransport.SRTP_AES128_CM_SHA1_80``), just sourced from
HKDF instead of the DTLS exporter. ``SRTPProtectionProfile.get_key_and_salt``
already does the correct client/server key+salt splitting for that layout -
reused as-is here, only the source of the 60 bytes changes.
"""

from __future__ import annotations

import hashlib
import hmac

from aiortc.rtcdtlstransport import SRTP_AES128_CM_SHA1_80, RTCDtlsTransport
from pylibsrtp import Policy, Session

_HKDF_INFO = b"stream_hkdf_webrtc_key"
_HKDF_LENGTH = 60  # 2 x (16-byte key + 14-byte salt), see module docstring

_original_setup_srtp = RTCDtlsTransport._setup_srtp
_installed = False


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_srtp_key(password: str, nonce: str, salt: str) -> bytes:
    """IKM = nonce + ":" + password, matching the decompiled Tapo Android
    app's derivation (``ub0.k.d`` -> HKDF-SHA256) exactly."""
    ikm = f"{nonce}:{password}".encode()
    return _hkdf_sha256(ikm=ikm, salt=salt.encode(), info=_HKDF_INFO, length=_HKDF_LENGTH)


def prepare_transport(dtls_transport: RTCDtlsTransport, srtp_key: bytes) -> None:
    """Call once per RTCDtlsTransport, before ``setRemoteDescription``
    triggers the DTLS handshake (i.e. right after obtaining the transport
    from a transceiver). Restricts the profile OpenSSL will negotiate to
    the one matching this key's byte layout, and stashes the key for
    ``_patched_setup_srtp`` to pick up once the handshake completes."""
    if len(srtp_key) != _HKDF_LENGTH:
        raise ValueError(f"Expected a {_HKDF_LENGTH}-byte SRTP key, got {len(srtp_key)}")
    dtls_transport._srtp_profiles = [SRTP_AES128_CM_SHA1_80]
    dtls_transport._tapo_srtp_key = srtp_key


def _patched_setup_srtp(self: RTCDtlsTransport) -> None:
    key = getattr(self, "_tapo_srtp_key", None)
    if key is None:
        _original_setup_srtp(self)
        return

    srtp_profile = SRTP_AES128_CM_SHA1_80
    if self._role == "server":
        srtp_tx_key = srtp_profile.get_key_and_salt(key, 1)
        srtp_rx_key = srtp_profile.get_key_and_salt(key, 0)
    else:
        srtp_tx_key = srtp_profile.get_key_and_salt(key, 0)
        srtp_rx_key = srtp_profile.get_key_and_salt(key, 1)

    rx_policy = Policy(
        key=srtp_rx_key, ssrc_type=Policy.SSRC_ANY_INBOUND, srtp_profile=srtp_profile.libsrtp_profile
    )
    rx_policy.allow_repeat_tx = True
    rx_policy.window_size = 1024
    self._rx_srtp = Session(rx_policy)

    tx_policy = Policy(
        key=srtp_tx_key, ssrc_type=Policy.SSRC_ANY_OUTBOUND, srtp_profile=srtp_profile.libsrtp_profile
    )
    tx_policy.allow_repeat_tx = True
    tx_policy.window_size = 1024
    self._tx_srtp = Session(tx_policy)


def install() -> None:
    """Idempotent - safe to call from multiple places (app startup +
    standalone test scripts)."""
    global _installed
    if _installed:
        return
    RTCDtlsTransport._setup_srtp = _patched_setup_srtp
    _installed = True
