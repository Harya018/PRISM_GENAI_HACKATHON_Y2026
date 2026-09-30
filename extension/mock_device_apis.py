"""Mock device APIs for the Samsung device-troubleshooting extension (Section B).

Same shape as Full-Duplex-Bench/v3's own mock_apis.py: plain synchronous functions, no external
calls, deterministic — so the extension is runnable and testable without real Samsung hardware
or cloud services, and so agent/commit_gate.py's `call_tool` callable works identically here via
`DeviceAPIRegistry.call(name, **kwargs)`.

Covers BOTH device families Samsung actually ships, because a support assistant that only knows
phones is wrong half the time: a Galaxy phone (One UI) and a Galaxy Book laptop (Windows). Every
tool takes a `device_type`, and the same topic returns genuinely different steps for each — the
Wi-Fi fix on One UI is not the Wi-Fi fix on Windows. `device_type` is also a normal tool
argument, which means it is subject to the commit gate and resolver like any other: "my phone —
no wait, my laptop" mid-sentence supersedes the phone call before it runs.
"""

from __future__ import annotations

from typing import Any, Dict

# Spoken synonyms -> canonical device type. The model is told to pass "phone" or "pc", but a
# realtime transcript will happily surface "laptop", "notebook", "my book", "windows" etc., and
# silently treating any of those as a phone is exactly the wrong-answer failure this map exists
# to prevent.
_PC_WORDS = ("pc", "laptop", "notebook", "computer", "desktop", "windows", "book", "galaxy book")
_PHONE_WORDS = ("phone", "mobile", "cell", "handset", "android", "one ui", "galaxy s", "tablet")


def _canon_device(device_type: Any) -> str:
    """Never guesses beyond these two families; anything unrecognised falls back to phone, which
    is the more common support case, and the caller can see what was used in the result."""
    text = str(device_type or "").strip().lower()
    if any(w in text for w in _PC_WORDS):
        return "pc"
    if any(w in text for w in _PHONE_WORDS):
        return "phone"
    return "phone"


_MANUAL_PHONE = {
    "wifi": "Settings > Connections > Wi-Fi — toggle off and on, then tap your network, choose "
            "Forget, and reconnect with the password.",
    "bluetooth": "Settings > Connections > Bluetooth > Scan — if the device was paired before, "
                 "tap the gear next to it and Unpair first, then pair again.",
    "battery": "Settings > Battery and device care > Battery > Background usage limits — put "
               "heavy apps to sleep. Remove the case while charging if it gets warm.",
    "screen": "Settings > Display > Motion smoothness — set to Standard. If it still flickers, "
              "Settings > Accessibility > Visibility enhancements > turn off Reduce animations.",
    "fingerprint": "Settings > Security and privacy > Biometrics > Fingerprints — delete the "
                   "saved fingerprint and register it again, and wipe the sensor area of the "
                   "screen. A screen protector over the sensor is a common cause.",
    "update": "Settings > Software update > Download and install.",
    "storage": "Settings > Battery and device care > Storage > Clean now.",
    "camera": "Clean the lens, then Settings > Apps > Camera > Storage > Clear cache.",
    "overheating": "Settings > Battery and device care > Battery > Background usage limits, and "
                   "take the case off while it charges.",
    "network": "Settings > General management > Reset > Reset network settings.",
}

_MANUAL_PC = {
    "wifi": "Settings > Network & internet > Wi-Fi — toggle off and on, then Forget the network "
            "and reconnect. If it keeps dropping, Device Manager > Network adapters > right-click "
            "your Wi-Fi adapter > Update driver.",
    "bluetooth": "Settings > Bluetooth & devices > Add device. If pairing fails, remove the "
                 "device first, then Device Manager > Bluetooth > Update driver.",
    "battery": "Settings > System > Power & battery > Battery usage — see which apps drain most, "
               "and set Power mode to Balanced.",
    "screen": "For flicker: Settings > System > Display > Advanced display — set the refresh rate "
              "to 60 Hz, then update the graphics driver in Device Manager > Display adapters. "
              "Samsung Settings > Display also has a separate brightness-flicker toggle.",
    "fingerprint": "Settings > Accounts > Sign-in options > Fingerprint recognition — remove the "
                   "enrolled fingerprint and set it up again. If the reader isn't listed at all, "
                   "update the biometric driver in Device Manager > Biometric devices.",
    "touchpad": "Settings > Bluetooth & devices > Touchpad — check it's switched on, then Reset "
                "touchpad settings at the bottom of that page.",
    "keyboard": "Settings > Bluetooth & devices > Touchpad and keyboard — if keys repeat or miss, "
                "update the keyboard driver in Device Manager > Keyboards.",
    "update": "Settings > Windows Update > Check for updates, then open Samsung Update for "
              "firmware and driver packages.",
    "storage": "Settings > System > Storage > Cleanup recommendations.",
    "camera": "Settings > Bluetooth & devices > Cameras — check the camera isn't disabled, and "
              "confirm the privacy shutter key (F10 on most Galaxy Books) isn't toggled off.",
    "overheating": "Close background apps, set Settings > System > Power & battery > Power mode "
                   "to Balanced, and make sure the underside vents aren't blocked.",
    "network": "Settings > Network & internet > Advanced network settings > Network reset.",
}

_MANUALS = {"phone": _MANUAL_PHONE, "pc": _MANUAL_PC}

# Topic synonyms so a spoken phrase lands on the right entry ("fingerprint scanner" -> fingerprint,
# "display" -> screen). Longest keys first at match time so "screen protector" can't shadow a more
# specific topic.
_TOPIC_ALIASES = {
    "display": "screen", "monitor": "screen", "flicker": "screen", "brightness": "screen",
    "finger print": "fingerprint", "fingerprint scanner": "fingerprint",
    "fingerprint reader": "fingerprint", "biometric": "fingerprint", "touch id": "fingerprint",
    "wi-fi": "wifi", "wi fi": "wifi", "internet": "wifi", "wireless": "wifi",
    "bluetooth pairing": "bluetooth", "pairing": "bluetooth",
    "trackpad": "touchpad", "mouse pad": "touchpad",
    "charging": "battery", "power": "battery", "drain": "battery",
    "heat": "overheating", "hot": "overheating", "temperature": "overheating",
    "software update": "update", "upgrade": "update", "firmware": "update",
    "space": "storage", "memory": "storage", "full": "storage",
}


def _resolve_topic(topic: str, manual: Dict[str, str]) -> str | None:
    text = str(topic or "").strip().lower()
    if not text:
        return None
    for alias in sorted(_TOPIC_ALIASES, key=len, reverse=True):
        if alias in text:
            candidate = _TOPIC_ALIASES[alias]
            if candidate in manual:
                return candidate
    for key in sorted(manual, key=len, reverse=True):
        if key in text:
            return key
    return None


def lookup_manual(topic: str, device_type: str = "phone") -> Dict[str, Any]:
    """Read-only. Looks up a troubleshooting snippet by topic keyword, for phone or PC."""
    dev = _canon_device(device_type)
    manual = _MANUALS[dev]
    key = _resolve_topic(topic, manual)
    if key is None:
        return {"status": "not_found", "topic": topic, "device_type": dev,
                "message": f"No {dev} manual entry found for that topic.",
                "available_topics": sorted(manual)}
    return {"status": "success", "topic": key, "device_type": dev, "snippet": manual[key]}


def get_device_status(device_type: str = "phone") -> Dict[str, Any]:
    """Read-only. Simulated live diagnostics snapshot for the requested device."""
    dev = _canon_device(device_type)
    if dev == "pc":
        return {
            "status": "success",
            "device_type": "pc",
            "model": "Galaxy Book4 Pro",
            "battery_percent": 48,
            "wifi_connected": True,
            "bluetooth_connected": False,
            "storage_free_gb": 84.2,
            "software_version": "Windows 11 23H2",
            "last_reboot_hours_ago": 3,
            "pending_driver_updates": 2,
        }
    return {
        "status": "success",
        "device_type": "phone",
        "model": "Galaxy S24",
        "battery_percent": 61,
        "wifi_connected": False,
        "bluetooth_connected": True,
        "storage_free_gb": 12.4,
        "software_version": "One UI 8.0",
        "last_reboot_hours_ago": 26,
    }


_PANEL_PATHS = {
    "phone": {
        "wi-fi": "Settings > Connections > Wi-Fi",
        "wifi": "Settings > Connections > Wi-Fi",
        "bluetooth": "Settings > Connections > Bluetooth",
        "battery": "Settings > Battery and device care > Battery",
        "display": "Settings > Display",
        "screen": "Settings > Display",
        "storage": "Settings > Battery and device care > Storage",
        "fingerprint": "Settings > Security and privacy > Biometrics > Fingerprints",
        "update": "Settings > Software update",
    },
    "pc": {
        "wi-fi": "Settings > Network & internet > Wi-Fi",
        "wifi": "Settings > Network & internet > Wi-Fi",
        "bluetooth": "Settings > Bluetooth & devices",
        "battery": "Settings > System > Power & battery",
        "display": "Settings > System > Display",
        "screen": "Settings > System > Display",
        "storage": "Settings > System > Storage",
        "fingerprint": "Settings > Accounts > Sign-in options",
        "touchpad": "Settings > Bluetooth & devices > Touchpad",
        "update": "Settings > Windows Update",
    },
}

_settings_state: Dict[str, Any] = {}


def open_settings(panel: str, device_type: str = "phone") -> Dict[str, Any]:
    """State-modifying, low-risk (navigational): simulates opening a settings panel. The real
    path differs per device family, so the result carries the one actually opened."""
    dev = _canon_device(device_type)
    key = str(panel or "").strip().lower()
    paths = _PANEL_PATHS[dev]
    path = next((paths[k] for k in sorted(paths, key=len, reverse=True) if k in key), None)
    _settings_state["last_opened_panel"] = panel
    _settings_state["last_device_type"] = dev
    return {"status": "success", "device_type": dev, "opened_panel": panel,
            "path": path or f"Settings > {panel}"}


def reset_network_settings(device_type: str = "phone") -> Dict[str, Any]:
    """State-modifying, RISKY: clears saved Wi-Fi networks, Bluetooth pairings, and (on a phone)
    mobile data settings. This function always simulates success once called — the confirmation-
    before-calling requirement is enforced by the commit gate (agent/commit_gate.py's
    `confirm_required`) and the agent's instructions, never here, matching how the benchmark's own
    mock tools never gate on anything themselves."""
    dev = _canon_device(device_type)
    detail = ("Wi-Fi, Bluetooth and mobile data settings have been reset."
              if dev == "phone" else
              "Wi-Fi and Bluetooth settings have been reset; saved networks were removed.")
    return {"status": "success", "device_type": dev,
            "message": detail + " You'll need to reconnect."}


class DeviceAPIRegistry:
    """Same `call(name, **kwargs)` shape as FDB-v3's `MockAPIRegistry`."""

    _FUNCS = {
        "lookup_manual": lookup_manual,
        "get_device_status": get_device_status,
        "open_settings": open_settings,
        "reset_network_settings": reset_network_settings,
    }

    def call(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        func = self._FUNCS.get(name)
        if func is None:
            raise ValueError(f"Unknown tool: {name}")
        return func(**kwargs)
