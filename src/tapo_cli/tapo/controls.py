"""Camera controls exposed in the live view.

The catalog below covers the controls common across Tapo models. It is NOT
camera-specific: ``probe_controls`` calls each getter against the actual camera
and keeps only the ones it supports, so every model renders its own capabilities
(a spotlight camera shows the spotlight, a pan-tilt camera shows auto-track, etc.).
Controls the camera rejects are silently skipped.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("tapo_cli.tapo.controls")


def _enabled(value: Any) -> bool:
    """Normalize the many on/off shapes Tapo returns into a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("on", "true", "1", "enabled", "open")
    if isinstance(value, dict):
        for k in ("enabled", "value", "status", "state"):
            if k in value:
                return _enabled(value[k])
    return bool(value)


class Control:
    def __init__(
        self,
        key: str,
        label: str,
        group: str,
        ctype: str,  # "toggle" | "select"
        getter: Callable[[Any], Any],
        setter: Callable[[Any, Any], Any],
        options: list[str] | None = None,
        help: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.group = group
        self.type = ctype
        self.options = options
        self.help = help
        self._get = getter
        self._set = setter

    def get(self, tapo: Any) -> Any:
        return self._get(tapo)

    def set(self, tapo: Any, value: Any) -> Any:
        return self._set(tapo, value)


def _toggle(key, label, group, getter, setter, help=None) -> Control:
    """A toggle whose getter is normalized to bool and setter takes a bool."""
    return Control(key, label, group, "toggle", lambda t: _enabled(getter(t)),
                   lambda t, v: setter(t, bool(v)), help=help)


# Catalog in display order. Grouped for the UI. Every entry is capability-gated.
CONTROLS: list[Control] = [
    # Image & display
    Control(
        "night_vision", "Night vision", "Image & display", "select",
        lambda t: str(t.getDayNightMode()),
        lambda t, v: t.setDayNightMode(str(v)),
        options=["auto", "on", "off"], help="IR / day-night mode",
    ),
    _toggle("flip", "Flip image 180°", "Image & display",
            lambda t: t.getImageFlipVertical(), lambda t, v: t.setImageFlipVertical(v)),
    _toggle("hdr", "HDR", "Image & display",
            lambda t: t.getHDR(), lambda t, v: t.setHDR(v)),
    _toggle("ldc", "Lens distortion correction", "Image & display",
            lambda t: t.getLensDistortionCorrection(), lambda t, v: t.setLensDistortionCorrection(v)),

    # Light & audio
    _toggle("spotlight", "Spotlight", "Light & audio",
            lambda t: t.getForceWhitelampState(), lambda t, v: t.setForceWhitelampState(v),
            help="Manual white spotlight"),
    _toggle("led", "Status LED", "Light & audio",
            lambda t: t.getLED(), lambda t, v: t.setLEDEnabled(v)),
    _toggle("record_audio", "Record audio", "Light & audio",
            lambda t: t.getRecordAudio(), lambda t, v: t.setRecordAudio(v)),

    # Detection
    _toggle("motion", "Motion detection", "Detection",
            lambda t: t.getMotionDetection(), lambda t, v: t.setMotionDetection(enabled=v)),
    _toggle("person", "Person detection", "Detection",
            lambda t: t.getPersonDetection(), lambda t, v: t.setPersonDetection(v)),
    _toggle("vehicle", "Vehicle detection", "Detection",
            lambda t: t.getVehicleDetection(), lambda t, v: t.setVehicleDetection(v)),
    _toggle("pet", "Pet detection", "Detection",
            lambda t: t.getPetDetection(), lambda t, v: t.setPetDetection(v)),
    _toggle("baby_cry", "Baby-cry detection", "Detection",
            lambda t: t.getBabyCryDetection(), lambda t, v: t.setBabyCryDetection(v)),
    _toggle("bark", "Dog-bark detection", "Detection",
            lambda t: t.getBarkDetection(), lambda t, v: t.setBarkDetection(v)),
    _toggle("meow", "Cat-meow detection", "Detection",
            lambda t: t.getMeowDetection(), lambda t, v: t.setMeowDetection(v)),
    _toggle("glass_break", "Glass-break detection", "Detection",
            lambda t: t.getGlassBreakDetection(), lambda t, v: t.setGlassBreakDetection(v)),
    _toggle("tamper", "Tamper detection", "Detection",
            lambda t: t.getTamperDetection(), lambda t, v: t.setTamperDetection(v)),
    _toggle("line_crossing", "Line-crossing detection", "Detection",
            lambda t: t.getLinecrossingDetection(), lambda t, v: t.setLinecrossingDetection(v)),
    _toggle("package", "Package detection", "Detection",
            lambda t: t.getPackageDetection(), lambda t, v: t.setPackageDetection(v)),

    # Movement (pan-tilt cameras)
    _toggle("auto_track", "Auto-track motion", "Movement",
            lambda t: t.getAutoTrackTarget(), lambda t, v: t.setAutoTrackTarget(v)),

    # Alerts & privacy
    _toggle("alarm", "Alarm siren", "Alerts & privacy",
            lambda t: t.getAlarm(), lambda t, v: t.setAlarm(v),
            help="Arms the light + sound alarm on detection"),
    _toggle("privacy", "Privacy mode", "Alerts & privacy",
            lambda t: t.getPrivacyMode(), lambda t, v: t.setPrivacyMode(v),
            help="Lens cover — blacks out the camera"),
]

_BY_KEY = {c.key: c for c in CONTROLS}


def probe_controls(tapo: Any) -> list[dict[str, Any]]:
    """Return the controls this specific camera supports, with current values."""
    out: list[dict[str, Any]] = []
    for c in CONTROLS:
        try:
            value = c.get(tapo)
        except Exception as exc:  # noqa: BLE001 — unsupported on this model
            log.debug("control %s unsupported: %s", c.key, exc)
            continue
        out.append(
            {"key": c.key, "label": c.label, "group": c.group, "type": c.type,
             "options": c.options, "help": c.help, "value": value}
        )
    return out


def apply_control(tapo: Any, key: str, value: Any) -> Any:
    """Set a control and return its refreshed value."""
    control = _BY_KEY.get(key)
    if control is None:
        raise KeyError(key)
    control.set(tapo, value)
    try:
        return control.get(tapo)
    except Exception:  # noqa: BLE001
        return value
