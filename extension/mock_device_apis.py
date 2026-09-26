"""Mock device APIs for the Samsung device-troubleshooting extension (Section B).

Same shape as Full-Duplex-Bench/v3's own mock_apis.py: plain synchronous functions, no external
calls, deterministic — so the extension is runnable and testable without real Samsung hardware
or cloud services, and so agent/commit_gate.py's `call_tool` callable works identically here via
`DeviceAPIRegistry.call(name, **kwargs)`.
"""

from __future__ import annotations

from typing import Any, Dict

_MANUAL_SNIPPETS = {
    "wifi": "To reconnect Wi-Fi: Settings > Connections > Wi-Fi > toggle off/on, then reselect "
           "your network.",
    "bluetooth": "To pair Bluetooth: Settings > Connections > Bluetooth > Scan > select the "
                "device.",
    "battery": "For battery drain: Settings > Battery and device care > Battery > Background "
              "usage limits.",
    "screen": "For screen flicker: Settings > Display > Motion smoothness > set to Standard.",
    "update": "To check for a software update: Settings > Software update > Download and "
             "install.",
    "storage": "To free up storage: Settings > Battery and device care > Storage > Clean now.",
    "camera": "For camera focus issues: clean the lens, then Settings > Apps > Camera > "
             "Storage > Clear cache.",
    "network": "For persistent network issues: Settings > General management > Reset > Reset "
              "network settings.",
}


def lookup_manual(topic: str) -> Dict[str, Any]:
    """Read-only. Looks up a troubleshooting snippet by topic keyword."""
    key = next((k for k in _MANUAL_SNIPPETS if k in topic.lower()), None)
    if key is None:
        return {"status": "not_found", "topic": topic,
                "message": "No manual entry found for that topic."}
    return {"status": "success", "topic": key, "snippet": _MANUAL_SNIPPETS[key]}


def get_device_status() -> Dict[str, Any]:
    """Read-only. Simulated live device diagnostics snapshot."""
    return {
        "status": "success",
        "battery_percent": 61,
        "wifi_connected": False,
        "bluetooth_connected": True,
        "storage_free_gb": 12.4,
        "software_version": "One UI 8.0",
        "last_reboot_hours_ago": 26,
    }


_settings_state: Dict[str, Any] = {}


def open_settings(panel: str) -> Dict[str, Any]:
    """State-modifying, low-risk (navigational): simulates opening a settings panel."""
    _settings_state["last_opened_panel"] = panel
    return {"status": "success", "opened_panel": panel}


def reset_network_settings() -> Dict[str, Any]:
    """State-modifying, RISKY: clears saved Wi-Fi networks, Bluetooth pairings, and mobile data
    settings. This function always simulates success once called — the confirmation-before-
    calling requirement is enforced by the agent's own instructions
    (extension/device_instructions.py), not here, matching how the benchmark's own mock tools
    never gate on anything themselves."""
    return {"status": "success",
            "message": "Network settings have been reset. Wi-Fi and Bluetooth will need to be "
                       "reconnected."}


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
