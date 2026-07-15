"""Translate between aiortc's SDP objects and TP-Link's flat SdpDetailInfo
field bag (see webrtc_signaling.SdpDetail) - TP-Link's protocol carries SDP
offer/answer as structured fields, not a raw SDP blob, so this is the glue
between "what aiortc understands" and "what the camera's signaling API
expects", in both directions.
"""

from __future__ import annotations

import re

from aiortc import RTCPeerConnection, RTCSessionDescription

from .webrtc_signaling import SdpDetail


async def build_offer(pc: RTCPeerConnection) -> SdpDetail:
    """Add a recvonly video transceiver, create the offer, and wait for ICE
    gathering to finish - TP-Link's protocol takes a fixed candidate list
    in one shot (no trickle ICE), so the offer must be fully gathered
    before we read it."""
    pc.addTransceiver("video", direction="recvonly")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    if pc.iceGatheringState != "complete":
        import asyncio

        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _on_change() -> None:
            if pc.iceGatheringState == "complete":
                done.set()

        await asyncio.wait_for(done.wait(), timeout=10.0)

    return _parse_sdp_to_detail(pc.localDescription.sdp)


def _parse_sdp_to_detail(sdp: str) -> SdpDetail:
    ice_ufrag = _first(sdp, r"a=ice-ufrag:(\S+)")
    ice_pwd = _first(sdp, r"a=ice-pwd:(\S+)")
    fp_match = re.search(r"a=fingerprint:(\S+) (\S+)", sdp)
    if not fp_match:
        raise ValueError("No a=fingerprint line in generated SDP")
    fingerprint = f"{fp_match.group(1)} {fp_match.group(2)}"

    h264_pt = _find_payload_type(sdp, "H264")
    h265_pt = _find_payload_type(sdp, "H265")

    candidates = [f"candidate:{line.split('a=candidate:', 1)[1]}" for line in sdp.splitlines() if line.startswith("a=candidate:")]

    if ice_ufrag is None or ice_pwd is None:
        raise ValueError("Missing ice-ufrag/ice-pwd in generated SDP")

    return SdpDetail(
        ice_ufrag=ice_ufrag,
        ice_pwd=ice_pwd,
        fingerprint=fingerprint,
        h264_payload_type=h264_pt,
        h265_payload_type=h265_pt,
        candidates=candidates or None,
    )


def _first(sdp: str, pattern: str) -> str | None:
    m = re.search(pattern, sdp)
    return m.group(1) if m else None


def _find_payload_type(sdp: str, codec_name: str) -> int | None:
    m = re.search(rf"a=rtpmap:(\d+) {codec_name}/", sdp)
    return int(m.group(1)) if m else None


def build_answer_sdp(offer_sdp: str, answer: SdpDetail, role: str = "active") -> str:
    """Reconstruct a syntactically valid SDP answer from TP-Link's flat
    field bag, matching the structure of our own offer (same m=video line,
    payload types, session identifiers) so aiortc's parser accepts it.

    role: DTLS setup role for the answer's a=setup line. We offer
    "actpass" (aiortc's default for an offer); the answer must pick a
    concrete role - "active" (camera acts as DTLS client) unless proven
    otherwise by a real handshake attempt.
    """
    origin_match = re.search(r"^o=(.+)$", offer_sdp, re.MULTILINE)
    session_line = origin_match.group(0) if origin_match else "o=- 0 0 IN IP4 0.0.0.0"

    mid_match = re.search(r"a=mid:(\S+)", offer_sdp)
    mid = mid_match.group(1) if mid_match else "0"

    payload_type = answer.h264_payload_type or answer.h265_payload_type
    if payload_type is None:
        raise ValueError("Answer has no usable video payload type")

    # aiortc's codec negotiation needs the answer's rtpmap/fmtp/rtcp-fb
    # lines for the chosen payload type, not just a bare rtpmap - a first
    # attempt without this failed with "Failed to set remote video
    # description send parameters" even though the SDP parsed fine.
    # These are OUR OWN declared codec parameters (from the offer this
    # answer is responding to), so copying them verbatim is correct - a
    # real answer selecting this payload type must be compatible with them.
    codec_lines = [
        line
        for line in offer_sdp.splitlines()
        if re.match(rf"a=(rtpmap|fmtp|rtcp-fb):{payload_type}\b", line)
    ]
    if not codec_lines:
        raise ValueError(f"Payload type {payload_type} not found in our own offer SDP")

    lines = [
        "v=0",
        session_line,
        "s=-",
        "t=0 0",
        f"a=group:BUNDLE {mid}",
        "a=msid-semantic: WMS",
        f"m=video 9 UDP/TLS/RTP/SAVPF {payload_type}",
        "c=IN IP4 0.0.0.0",
        "a=rtcp:9 IN IP4 0.0.0.0",
        f"a=ice-ufrag:{answer.ice_ufrag}",
        f"a=ice-pwd:{answer.ice_pwd}",
        f"a=fingerprint:{answer.fingerprint.split(' ', 1)[0]} {answer.fingerprint.split(' ', 1)[1]}",
        f"a=setup:{role}",
        f"a=mid:{mid}",
        "a=recvonly",
        "a=rtcp-mux",
        *codec_lines,
    ]
    if answer.video_ssrc is not None:
        lines.append(f"a=ssrc:{answer.video_ssrc} cname:tapo")
    lines.extend(f"a={c}" for c in (answer.candidates or []))

    return "\r\n".join(lines) + "\r\n"


async def apply_answer(pc: RTCPeerConnection, answer: SdpDetail) -> None:
    offer_sdp = pc.localDescription.sdp
    answer_sdp = build_answer_sdp(offer_sdp, answer)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
