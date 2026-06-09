"""Camera controls exposed in the live view.

A model-agnostic catalog covering the user-facing controls pytapo exposes. Each
control declares a getter/capability check; ``probe_controls`` runs them against
the actual camera and keeps only what it supports, so every model renders its own
capabilities (a spotlight camera shows the spotlight, a pan-tilt camera shows the
PTZ pad + auto-track, a PIR sensor shows PIR sensitivity, …).

Control types: ``toggle`` (bool), ``select`` (enum), ``number`` (slider),
``action`` (button), ``ptz`` (pan/tilt pad).

Deliberately excluded as live-view controls (footguns): SD-card *format* (wipes
recordings) and media-encrypt (can break the stream), plus pure system config
(timezone, firmware, battery, OSD).
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


def _to_int(value: Any, *keys: str) -> int:
    if isinstance(value, dict):
        for k in keys or ("value", "sensitivity", "volume"):
            if k in value:
                return _to_int(value[k])
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class Control:
    def __init__(
        self,
        key: str,
        label: str,
        group: str,
        ctype: str,
        desc: str | None = None,
        options: list[str] | None = None,
        vmin: int | None = None,
        vmax: int | None = None,
        vstep: int | None = None,
        danger: bool = False,
        default: Any = None,
        getter: Callable[[Any], Any] | None = None,
        setter: Callable[[Any, Any], Any] | None = None,
        capability: Callable[[Any], Any] | None = None,
        action: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.group = group
        self.type = ctype
        self.desc = desc
        self.options = options
        self.vmin = vmin
        self.vmax = vmax
        self.vstep = vstep
        self.danger = danger
        self.default = default
        self._get = getter
        self._set = setter
        self._cap = capability
        self._action = action

    @property
    def getter(self):
        return self._get

    @property
    def action(self):
        return self._action

    def supported(self, tapo: Any) -> bool:
        if self._cap is not None:
            try:
                if self._cap(tapo) is False:
                    return False
            except Exception:  # noqa: BLE001 — capability not present
                return False
        return True

    def to_dict(self, value: Any) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "group": self.group,
            "type": self.type, "desc": self.desc, "options": self.options,
            "min": self.vmin, "max": self.vmax, "step": self.vstep,
            "danger": self.danger, "value": value,
        }


def _toggle(key, label, group, desc, getter, setter, capability=None) -> Control:
    return Control(
        key, label, group, "toggle", desc=desc, default=False,
        getter=(lambda t: _enabled(getter(t))) if getter else None,
        setter=(lambda t, v: setter(t, bool(v))) if setter else None,
        capability=capability,
    )


def _ptz_move(tapo: Any, direction: Any) -> Any:
    deg = 15
    moves = {"up": (0, deg), "down": (0, -deg), "left": (-deg, 0), "right": (deg, 0)}
    x, y = moves.get(str(direction), (0, 0))
    return tapo.moveMotor(x, y)


# --------------------------------------------------------------------------- #
# Catalog (display order). Every entry is capability-gated by probe_controls.
# --------------------------------------------------------------------------- #
CONTROLS: list[Control] = [
    # ---- Image & display -------------------------------------------------- #
    Control("night_vision", "Night vision", "Image & display", "select",
            desc="Day, night (infrared), or automatic switching.",
            options=["auto", "on", "off"],
            getter=lambda t: str(t.getDayNightMode()),
            setter=lambda t, v: t.setDayNightMode(str(v))),
    Control("light_frequency", "Anti-flicker", "Image & display", "select",
            desc="Match your mains frequency to stop flicker under artificial light.",
            options=["auto", "50", "60"],
            getter=lambda t: str(t.getLightFrequencyMode()),
            setter=lambda t, v: t.setLightFrequencyMode(str(v))),
    _toggle("flip", "Flip image 180°", "Image & display",
            "Rotate the picture for upside-down (ceiling) mounting.",
            lambda t: t.getImageFlipVertical(), lambda t, v: t.setImageFlipVertical(v)),
    _toggle("hdr", "HDR", "Image & display",
            "High dynamic range — balances very bright and dark areas.",
            lambda t: t.getHDR(), lambda t, v: t.setHDR(v)),
    _toggle("ldc", "Distortion correction", "Image & display",
            "Straightens the wide-angle (fisheye) lens curvature.",
            lambda t: t.getLensDistortionCorrection(), lambda t, v: t.setLensDistortionCorrection(v)),

    # ---- Light & LED ------------------------------------------------------ #
    _toggle("spotlight", "Spotlight", "Light & LED",
            "Turn the camera's white spotlight on or off.",
            lambda t: t.getForceWhitelampState(), lambda t, v: t.setForceWhitelampState(v)),
    _toggle("led", "Status LED", "Light & LED",
            "The small indicator light on the camera body.",
            lambda t: t.getLED(), lambda t, v: t.setLEDEnabled(v)),

    # ---- Audio ------------------------------------------------------------ #
    Control("speaker_volume", "Speaker volume", "Audio", "number",
            desc="Loudness of the camera's speaker (two-way talk and alarm).",
            vmin=0, vmax=100, vstep=1,
            getter=lambda t: _to_int(t.getAudioConfig().get("audio_config", {}).get("speaker", {}), "volume"),
            setter=lambda t, v: t.setSpeakerVolume(int(v))),
    Control("microphone_volume", "Microphone volume", "Audio", "number",
            desc="Sensitivity of the camera's microphone.",
            vmin=0, vmax=100, vstep=1,
            getter=lambda t: _to_int(t.getAudioConfig().get("audio_config", {}).get("microphone", {}), "volume"),
            setter=lambda t, v: t.setMicrophone(volume=int(v))),
    _toggle("record_audio", "Record audio", "Audio",
            "Include sound in recordings.",
            lambda t: t.getRecordAudio(), lambda t, v: t.setRecordAudio(v)),

    # ---- Detection -------------------------------------------------------- #
    _toggle("motion", "Motion detection", "Detection",
            "Trigger on any movement in view.",
            lambda t: t.getMotionDetection(), lambda t, v: t.setMotionDetection(enabled=v)),
    _toggle("person", "Person detection", "Detection",
            "Trigger specifically when a person is seen.",
            lambda t: t.getPersonDetection(), lambda t, v: t.setPersonDetection(v)),
    _toggle("vehicle", "Vehicle detection", "Detection",
            "Trigger when a vehicle is seen.",
            lambda t: t.getVehicleDetection(), lambda t, v: t.setVehicleDetection(v)),
    _toggle("pet", "Pet detection", "Detection",
            "Trigger when a pet/animal is seen.",
            lambda t: t.getPetDetection(), lambda t, v: t.setPetDetection(v)),
    _toggle("baby_cry", "Baby-cry detection", "Detection",
            "Listen for a baby crying.",
            lambda t: t.getBabyCryDetection(), lambda t, v: t.setBabyCryDetection(v)),
    _toggle("bark", "Dog-bark detection", "Detection",
            "Listen for a dog barking.",
            lambda t: t.getBarkDetection(), lambda t, v: t.setBarkDetection(v)),
    _toggle("meow", "Cat-meow detection", "Detection",
            "Listen for a cat meowing.",
            lambda t: t.getMeowDetection(), lambda t, v: t.setMeowDetection(v)),
    _toggle("glass_break", "Glass-break detection", "Detection",
            "Listen for the sound of breaking glass.",
            lambda t: t.getGlassBreakDetection(), lambda t, v: t.setGlassBreakDetection(v)),
    _toggle("tamper", "Tamper detection", "Detection",
            "Alert if the camera is covered, blinded, or moved.",
            lambda t: t.getTamperDetection(), lambda t, v: t.setTamperDetection(v)),
    _toggle("line_crossing", "Line-crossing", "Detection",
            "Trigger when something crosses a virtual line you set in the Tapo app.",
            lambda t: t.getLinecrossingDetection(), lambda t, v: t.setLinecrossingDetection(v)),
    _toggle("package", "Package detection", "Detection",
            "Detect a package left in the monitored area.",
            lambda t: t.getPackageDetection(), lambda t, v: t.setPackageDetection(v)),
    Control("pir_sensitivity", "PIR sensitivity", "Detection", "number",
            desc="Sensitivity of the passive-infrared motion sensor.",
            vmin=0, vmax=100, vstep=1,
            getter=lambda t: _to_int(t.getPirSensitivity(), "sensitivity", "value"),
            setter=lambda t, v: t.setPirSensitivity(int(v))),

    # ---- Movement (pan-tilt models only) ---------------------------------- #
    Control("ptz", "Pan / tilt", "Movement", "ptz",
            desc="Move the camera. Use the arrows to pan and tilt.",
            capability=lambda t: t.getMotorCapability(),
            action=_ptz_move),
    _toggle("auto_track", "Auto-track motion", "Movement",
            "Automatically pan/tilt to follow detected motion.",
            lambda t: t.getAutoTrackTarget(), lambda t, v: t.setAutoTrackTarget(v),
            capability=lambda t: t.getMotorCapability()),

    # ---- Alerts & privacy ------------------------------------------------- #
    _toggle("alarm", "Alarm (on detection)", "Alerts & privacy",
            "Arm the light + sound alarm to fire when something is detected.",
            lambda t: t.getAlarm(), lambda t, v: t.setAlarm(v)),
    Control("siren", "Sound siren now", "Alerts & privacy", "toggle",
            desc="Trigger the siren immediately (turn off to silence it).",
            default=False,
            capability=lambda t: t.getAlarm(),
            action=lambda t, v: t.startManualAlarm() if v else t.stopManualAlarm()),
    _toggle("privacy", "Privacy mode", "Alerts & privacy",
            "Privacy / lens cover — stops the camera seeing anything.",
            lambda t: t.getPrivacyMode(), lambda t, v: t.setPrivacyMode(v)),

    # ---- System ----------------------------------------------------------- #
    Control("reboot", "Reboot camera", "System", "action",
            desc="Restart the camera. It will be offline for ~30 seconds.",
            danger=True,
            action=lambda t, v: t.reboot()),
]

_BY_KEY = {c.key: c for c in CONTROLS}

# Which controls each camera supports is stable, but their *values* change. We
# cache the supported-key set per camera so repeat probes skip the (slow)
# support detection of unsupported controls and only re-read current values.
_capability_cache: dict[int, list[str]] = {}


def clear_capability_cache(camera_id: int | None = None) -> None:
    """Forget cached capabilities (call when a camera's host/credentials change)."""
    if camera_id is None:
        _capability_cache.clear()
    else:
        _capability_cache.pop(camera_id, None)


def probe_controls(tapo: Any, camera_id: int | None = None) -> list[dict[str, Any]]:
    """Return the controls this specific camera supports, with current values.

    On the first call for a camera the full capability probe runs and the
    supported-key set is cached; subsequent calls only re-read those controls'
    values (much fewer round-trips).
    """
    cached = _capability_cache.get(camera_id) if camera_id is not None else None

    if cached is not None:
        out: list[dict[str, Any]] = []
        for key in cached:
            c = _BY_KEY.get(key)
            if c is None:
                continue
            value = c.default
            if c.getter is not None:
                try:
                    value = c.getter(tapo)
                except Exception as exc:  # noqa: BLE001 — transient; keep showing the control
                    log.debug("control %s value read failed: %s", key, exc)
            out.append(c.to_dict(value))
        return out

    out = []
    for c in CONTROLS:
        if not c.supported(tapo):
            log.debug("control %s not supported", c.key)
            continue
        value = c.default
        if c.getter is not None:
            try:
                value = c.getter(tapo)
            except Exception as exc:  # noqa: BLE001 — unsupported on this model
                log.debug("control %s getter failed: %s", c.key, exc)
                continue
        out.append(c.to_dict(value))

    if camera_id is not None:
        _capability_cache[camera_id] = [d["key"] for d in out]
    return out


def apply_control(tapo: Any, key: str, value: Any) -> Any:
    """Set a control (or run an action) and return its resulting value."""
    control = _BY_KEY.get(key)
    if control is None:
        raise KeyError(key)
    if control.action is not None:
        control.action(tapo, value)
        return value if control.type == "toggle" else None
    control._set(tapo, value)
    if control.getter is not None:
        try:
            return control.getter(tapo)
        except Exception:  # noqa: BLE001
            return value
    return value
