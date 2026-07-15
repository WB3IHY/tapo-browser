"""Minimal, incremental MPEG-TS demuxer for one purpose: extracting the raw
G.711 A-law audio elementary stream from the camera's mpegts response.

Why this exists: ffmpeg's own mpegts demuxer can't classify this stream at
all. TP-Link tags it with stream_type 144 (0x90) - a private/vendor value
with no meaning in any MPEG-TS/DVB/ATSC spec (confirmed via the Tapo Android
app's own decompiled source: it hardcodes exactly this value as PCMA, the
same way this module does - there's no auto-detection happening on their
side either, they just know their own protocol). ffmpeg has no CLI mechanism
to reclassify an already-"unknown"-typed stream into a specific codec type,
even when given the exact decoder name - confirmed via direct testing, not
assumed. So: demux the audio PID ourselves, strip PES headers to get raw
A-law samples, and hand ffmpeg those bytes as a separately-typed input
(`-f alaw`) instead of asking it to find them itself.

Deliberately minimal: only tracks the one PAT/PMT structure needed to find
the audio PID, assumes a single-packet PAT and single-packet PMT (true for
this camera's stream - a section spanning multiple TS packets would need
more state than this handles), and only extracts payload for the one PID of
interest. Not a general-purpose demuxer.
"""

from __future__ import annotations

_PACKET_SIZE = 188
_SYNC_BYTE = 0x47
_PAT_PID = 0x0000
_AUDIO_STREAM_TYPE = 144  # PCMA / G.711 A-law - see module docstring


def _parse_pat(payload: bytes) -> int | None:
    """Return the PMT PID from a single-packet PAT section, or None."""
    if not payload:
        return None
    pointer_field = payload[0]
    section = payload[1 + pointer_field :]
    if len(section) < 12 or section[0] != 0x00:  # table_id for PAT
        return None
    section_length = ((section[1] & 0x0F) << 8) | section[2]
    # Program loop starts at byte 8, ends before the 4-byte CRC.
    programs = section[8 : 3 + section_length - 4]
    for i in range(0, len(programs) - 3, 4):
        program_number = (programs[i] << 8) | programs[i + 1]
        pid = ((programs[i + 2] & 0x1F) << 8) | programs[i + 3]
        if program_number != 0:  # skip the network-PID entry
            return pid
    return None


def _parse_pmt(payload: bytes) -> int | None:
    """Return the audio elementary stream's PID from a single-packet PMT
    section, or None if no stream is tagged with the audio stream_type."""
    if not payload:
        return None
    pointer_field = payload[0]
    section = payload[1 + pointer_field :]
    if len(section) < 12 or section[0] != 0x02:  # table_id for PMT
        return None
    section_length = ((section[1] & 0x0F) << 8) | section[2]
    program_info_length = ((section[10] & 0x0F) << 8) | section[11]
    pos = 12 + program_info_length
    end = 3 + section_length - 4  # before the 4-byte CRC
    while pos + 5 <= end and pos + 5 <= len(section):
        stream_type = section[pos]
        elementary_pid = ((section[pos + 1] & 0x1F) << 8) | section[pos + 2]
        es_info_length = ((section[pos + 3] & 0x0F) << 8) | section[pos + 4]
        if stream_type == _AUDIO_STREAM_TYPE:
            return elementary_pid
        pos += 5 + es_info_length
    return None


def _pes_payload(packet_payload: bytes, is_start: bool, carry: bytes) -> tuple[bytes, bytes]:
    """Strip a PES header from the first packet of a PES packet, or pass
    continuation payload through unchanged. Returns (audio_bytes, new_carry)
    - carry isn't currently used (single-packet PES headers only) but kept
    for a future multi-packet-header edge case rather than silently
    truncating data if one is ever seen.
    """
    if not is_start:
        return packet_payload, carry
    if len(packet_payload) < 9 or packet_payload[:3] != b"\x00\x00\x01":
        return b"", carry
    pes_header_data_length = packet_payload[8]
    header_end = 9 + pes_header_data_length
    return packet_payload[header_end:], carry


class AudioExtractor:
    """Feed raw 188-byte TS packets in; get back raw G.711 A-law elementary
    stream bytes for the one audio PID discovered via PAT/PMT, or empty
    bytes for anything else."""

    def __init__(self) -> None:
        self._pmt_pid: int | None = None
        self._audio_pid: int | None = None
        self._carry = b""

    def feed(self, packet: bytes) -> bytes:
        if len(packet) != _PACKET_SIZE or packet[0] != _SYNC_BYTE:
            return b""

        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        is_start = bool(packet[1] & 0x40)
        adaptation_field_control = (packet[3] >> 4) & 0x3

        pos = 4
        if adaptation_field_control in (2, 3):  # adaptation field present
            if pos >= len(packet):
                return b""
            adaptation_length = packet[pos]
            pos += 1 + adaptation_length
        if adaptation_field_control in (0, 2):  # no payload
            return b""
        payload = packet[pos:]

        if pid == _PAT_PID:
            self._pmt_pid = _parse_pat(payload) if is_start else self._pmt_pid
            return b""
        if self._pmt_pid is not None and pid == self._pmt_pid:
            if is_start:
                found = _parse_pmt(payload)
                if found is not None:
                    self._audio_pid = found
            return b""
        if self._audio_pid is not None and pid == self._audio_pid:
            audio_bytes, self._carry = _pes_payload(payload, is_start, self._carry)
            return audio_bytes
        return b""
