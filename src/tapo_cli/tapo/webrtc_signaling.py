"""TP-Link cloud WebRTC signaling client (RtcApi: services-sync / sfu/request).

Reverse-engineered from the Tapo Android APK - see the project's WebRTC
investigation memory/notes. Distinct from cloud_auth.py's login endpoint
(an older, simpler, separately-discovered API): this one is the *current*
app's real signaling protocol, used only after a cloud login token exists.

Not yet live-tested - several details below are the best reconstruction
from static analysis and are flagged accordingly. Expect this to need the
same kind of iteration cloud_auth.py's login endpoint did.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi
import httpx

_TPLINK_CA_BUNDLE = Path(__file__).parent / "vendor" / "tplink_ca_bundle.pem"


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(_TPLINK_CA_BUNDLE))
    return ctx


# Empirically confirmed via a live packet capture of the real app talking to
# this account's own cameras (all in AWS us-east-1) - not derived from any
# per-account region lookup. A different TP-Link account/region would need a
# different prefix (aps1/euw1/... - seen as options in the decompile, exact
# selection logic not traced). Fine for this single-user personal app; would
# need to become configurable if ever generalized.
CIPC_API_BASE_URL = "https://use1-cipc-api.i.tplinkcloud.com"

# X-Source/X-Brand exact values unconfirmed from static analysis (unlike the
# endpoints/body shapes, which come from concrete decompiled code) - best
# guesses, flagged for adjustment if signaling calls get rejected the way
# the first login attempt shape did.
_COMMON_HEADERS_BASE = {
    "X-Ca-Type": "cloud-self",
    "X-Brand": "TAPO",
    "X-Source": "ANDROID",
    "Content-Type": "application/json; charset=UTF-8",
}


class SignalingError(Exception):
    def __init__(self, message: str, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class SdpDetail:
    """Mirrors the decompiled SdpDetailInfo field bag TP-Link sends/expects
    instead of a raw SDP blob - a structured translation of what a normal
    SDP offer/answer carries. See webrtc_sdp.py for the aiortc<->this
    translation."""

    ice_ufrag: str
    ice_pwd: str
    fingerprint: str  # "sha-256 AA:BB:..." format
    h264_payload_type: int | None = None
    h265_payload_type: int | None = None
    audio_payload_type: int | None = None
    video_ssrc: int | None = None
    audio_ssrc: int | None = None
    rtx_ssrc: int | None = None
    candidates: list[str] | None = None
    dtls_key: str | None = None  # only set on the plaintext-28800 LOCAL path, not used here
    sctp_encrypt: str = "aes-128-gcm"

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "iceUFrag": self.ice_ufrag,
            "icePwd": self.ice_pwd,
            "fingerPrint": self.fingerprint,
            "sctpEncrypt": self.sctp_encrypt,
        }
        if self.h264_payload_type is not None:
            d["h264PayloadType"] = self.h264_payload_type
        if self.h265_payload_type is not None:
            d["h265PayloadType"] = self.h265_payload_type
        if self.audio_payload_type is not None:
            d["audioPayloadType"] = self.audio_payload_type
        if self.video_ssrc is not None:
            d["videoSsrc"] = self.video_ssrc
        if self.audio_ssrc is not None:
            d["audioSsrc"] = self.audio_ssrc
        if self.rtx_ssrc is not None:
            d["rtxSsrc"] = self.rtx_ssrc
        if self.candidates is not None:
            d["candidates"] = self.candidates
        if self.dtls_key is not None:
            d["dtlsKey"] = self.dtls_key
        return d

    @staticmethod
    def from_wire(data: dict[str, Any]) -> "SdpDetail":
        return SdpDetail(
            ice_ufrag=data["iceUFrag"],
            ice_pwd=data["icePwd"],
            fingerprint=data["fingerPrint"],
            h264_payload_type=data.get("h264PayloadType"),
            h265_payload_type=data.get("h265PayloadType"),
            audio_payload_type=data.get("audioPayloadType"),
            video_ssrc=data.get("videoSsrc"),
            audio_ssrc=data.get("audioSsrc"),
            rtx_ssrc=data.get("rtxSsrc"),
            candidates=data.get("candidates"),
            sctp_encrypt=data.get("sctpEncrypt", "aes-128-gcm"),
        )


@dataclass
class SfuAnswer:
    error_code: int
    message: str | None
    sdp_detail: SdpDetail | None
    session_id: str | None


class RtcApiClient:
    def __init__(self, token: str, terminal_uuid: str, base_url: str = CIPC_API_BASE_URL) -> None:
        self._token = token
        self._terminal_uuid = terminal_uuid
        self._base_url = base_url

    def _headers(self, *, auth_prefix: str = "") -> dict[str, str]:
        return {
            **_COMMON_HEADERS_BASE,
            "Authorization": f"{auth_prefix}{self._token}",
            "X-Client-Id": self._terminal_uuid,
        }

    async def request_sfu(
        self, device_id: str, sdp_offer: SdpDetail, session_id: str, player_id: str, cloud_type: int = 2
    ) -> SfuAnswer:
        """POST {base}/v1/sfu/request - the actual per-session SDP exchange.

        No dtlsKey here: confirmed via decompile that the HKDF/SRTP-key
        override only applies to the separate LOCAL client type (unavailable
        on this project's cameras). This path uses plain, standard
        DTLS-SRTP - the RtcParam.dtlsKey field exists in the wire schema but
        is written-only by the LOCAL path's request and never read back
        anywhere, so it's omitted here rather than sent as a meaningless
        placeholder.

        cloud_type: 2 = device supports IoT cloud, 1 = doesn't - matches the
        decompiled builder's `isSupportIoTCloud() ? 2 : 1`. Defaults to 2:
        every camera this project supports already implies IoT-cloud support
        (services-sync itself requires it), so 1 would never apply here.

        player_id: the decompiled builder uses a separate stable per-install
        UUID (`pb0.b.getUuid()`) distinct from terminalUUID - not
        independently confirmed, so this reuses the same terminal_uuid value
        as a reasonable stand-in for "a stable per-install identifier".
        """
        body = {
            "deviceId": device_id,
            "sdpDetail": sdp_offer.to_wire(),
            "sctpEncrypt": sdp_offer.sctp_encrypt,
            "sessionId": session_id,
            "streamType": "playback",
            "source": "tapo-app",
            "cloudType": cloud_type,
            "playerId": player_id,
        }
        headers = self._headers(auth_prefix="")
        url = f"{self._base_url}/v1/sfu/request"
        async with httpx.AsyncClient(timeout=20.0, verify=_build_ssl_context()) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(body))
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                resp.raise_for_status()
                raise

        error_code = data.get("errorCode", 0)
        if error_code != 0:
            msg = data.get("msg") or data.get("message") or f"sfu/request failed (errorCode={error_code})"
            raise SignalingError(msg, error_code)

        sdp_detail = SdpDetail.from_wire(data["sdpDetail"]) if data.get("sdpDetail") else None
        return SfuAnswer(
            error_code=error_code,
            message=data.get("msg") or data.get("message"),
            sdp_detail=sdp_detail,
            session_id=data.get("sessionId"),
        )
