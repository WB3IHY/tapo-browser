"""Camera controls exposed in the live view (night vision, privacy, LED, etc.).

Each control is a small descriptor with a getter + setter operating on a pytapo
``Tapo`` client. Support varies by model/firmware, so ``probe_controls`` calls each
getter and silently skips the ones the camera rejects — the UI only shows what works.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("tapo_cli.tapo.controls")


def _enabled(value: Any) -> bool:
    """Normalize Tapo's {'enabled': 'on'|'off'} (or a bare bool) to a bool."""
    if isinstance(value, dict):
        return str(value.get("enabled", "")).lower() == "on"
    return bool(value)


class Control:
    def __init__(
        self,
        key: str,
        label: str,
        ctype: str,  # "toggle" | "select"
        getter: Callable[[Any], Any],
        setter: Callable[[Any, Any], Any],
        options: list[str] | None = None,
        help: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.type = ctype
        self.options = options
        self.help = help
        self._get = getter
        self._set = setter

    def get(self, tapo: Any) -> Any:
        return self._get(tapo)

    def set(self, tapo: Any, value: Any) -> Any:
        return self._set(tapo, value)


# Order here is the display order in the UI.
CONTROLS: list[Control] = [
    Control(
        "night_vision", "Night vision", "select",
        lambda t: str(t.getDayNightMode()),
        lambda t, v: t.setDayNightMode(str(v)),
        options=["auto", "on", "off"],
        help="IR / day-night mode",
    ),
    Control(
        "privacy", "Privacy mode", "toggle",
        lambda t: _enabled(t.getPrivacyMode()),
        lambda t, v: t.setPrivacyMode(bool(v)),
        help="Lens cover — blacks out the camera",
    ),
    Control(
        "led", "Status LED", "toggle",
        lambda t: _enabled(t.getLED()),
        lambda t, v: t.setLEDEnabled(bool(v)),
    ),
    Control(
        "motion", "Motion detection", "toggle",
        lambda t: _enabled(t.getMotionDetection()),
        lambda t, v: t.setMotionDetection(enabled=bool(v)),
    ),
    Control(
        "flip", "Flip image 180°", "toggle",
        lambda t: bool(t.getImageFlipVertical()),
        lambda t, v: t.setImageFlipVertical(bool(v)),
    ),
    Control(
        "alarm", "Alarm siren", "toggle",
        lambda t: _enabled(t.getAlarm()),
        lambda t, v: t.setAlarm(bool(v)),
        help="Arms the light + sound alarm on detection",
    ),
]

_BY_KEY = {c.key: c for c in CONTROLS}


def probe_controls(tapo: Any) -> list[dict[str, Any]]:
    """Return the controls this camera supports, with their current values."""
    out: list[dict[str, Any]] = []
    for c in CONTROLS:
        try:
            value = c.get(tapo)
        except Exception as exc:  # noqa: BLE001 — unsupported on this model
            log.debug("control %s unsupported: %s", c.key, exc)
            continue
        out.append(
            {"key": c.key, "label": c.label, "type": c.type, "options": c.options,
             "help": c.help, "value": value}
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
