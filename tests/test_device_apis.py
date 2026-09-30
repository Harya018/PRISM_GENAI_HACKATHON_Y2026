"""Tests for extension/mock_device_apis.py — specifically that the phone and PC device families
stay genuinely separate. The failure this guards against is the one that made the extension feel
broken in live testing: a user describing a laptop ("my laptop's screen is flickering", "the
fingerprint reader stopped working") getting One UI phone steps back, which are simply wrong for
them.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "extension"))

from mock_device_apis import (DeviceAPIRegistry, get_device_status, lookup_manual,
                              open_settings, reset_network_settings)


# --- device_type routing ---

def test_same_topic_returns_different_steps_per_device():
    phone = lookup_manual("wifi", device_type="phone")
    pc = lookup_manual("wifi", device_type="pc")
    assert phone["status"] == pc["status"] == "success"
    assert phone["snippet"] != pc["snippet"], "phone and PC must not share Wi-Fi steps"
    assert "Connections" in phone["snippet"], "phone steps should use the One UI path"
    assert "Network & internet" in pc["snippet"], "PC steps should use the Windows path"


def test_spoken_device_synonyms_route_to_pc():
    for spoken in ["laptop", "my laptop", "notebook", "computer", "Windows", "galaxy book"]:
        r = lookup_manual("screen", device_type=spoken)
        assert r["device_type"] == "pc", f"{spoken!r} should be treated as a PC"


def test_spoken_device_synonyms_route_to_phone():
    for spoken in ["phone", "my mobile", "Galaxy S24", "handset", "one ui"]:
        r = lookup_manual("screen", device_type=spoken)
        assert r["device_type"] == "phone", f"{spoken!r} should be treated as a phone"


def test_unrecognised_device_falls_back_to_phone_and_says_so():
    r = lookup_manual("wifi", device_type="toaster")
    assert r["device_type"] == "phone", "unknown devices fall back, they don't error"


# --- topic resolution (the live-test scenarios that previously found nothing) ---

def test_fingerprint_scanner_phrasing_resolves_on_both_devices():
    for spoken_topic in ["fingerprint scanner", "finger print", "fingerprint reader", "biometric"]:
        for dev in ["phone", "pc"]:
            r = lookup_manual(spoken_topic, device_type=dev)
            assert r["status"] == "success", f"{spoken_topic!r} on {dev} should resolve"
            assert r["topic"] == "fingerprint"


def test_display_flicker_phrasing_resolves_to_screen():
    for spoken_topic in ["display", "my display is flickering", "brightness", "monitor"]:
        r = lookup_manual(spoken_topic, device_type="pc")
        assert r["topic"] == "screen", f"{spoken_topic!r} should resolve to the screen entry"


def test_pc_only_topics_are_not_offered_for_a_phone():
    assert lookup_manual("touchpad", device_type="pc")["status"] == "success"
    assert lookup_manual("touchpad", device_type="phone")["status"] == "not_found", (
        "a phone has no touchpad entry -- it must say so, not return an unrelated snippet")


def test_unknown_topic_reports_not_found_with_the_available_list():
    r = lookup_manual("quantum flux capacitor", device_type="pc")
    assert r["status"] == "not_found"
    assert "touchpad" in r["available_topics"]


# --- status / settings / reset ---

def test_device_status_differs_per_family():
    phone = get_device_status(device_type="phone")
    pc = get_device_status(device_type="pc")
    assert phone["model"] == "Galaxy S24" and pc["model"] == "Galaxy Book4 Pro"
    assert "One UI" in phone["software_version"] and "Windows" in pc["software_version"]
    assert "pending_driver_updates" in pc and "pending_driver_updates" not in phone


def test_open_settings_returns_the_os_appropriate_path():
    assert open_settings("Wi-Fi", device_type="phone")["path"] == "Settings > Connections > Wi-Fi"
    assert open_settings("Wi-Fi", device_type="pc")["path"] == "Settings > Network & internet > Wi-Fi"


def test_open_settings_unknown_panel_still_succeeds_without_inventing_a_path():
    r = open_settings("Some Custom Panel", device_type="pc")
    assert r["status"] == "success"
    assert r["opened_panel"] == "Some Custom Panel"


def test_reset_message_mentions_mobile_data_only_on_a_phone():
    assert "mobile data" in reset_network_settings(device_type="phone")["message"]
    assert "mobile data" not in reset_network_settings(device_type="pc")["message"]


# --- registry wiring (the path CommitGate actually calls through) ---

def test_registry_passes_device_type_through():
    reg = DeviceAPIRegistry()
    r = reg.call("lookup_manual", topic="wifi", device_type="laptop")
    assert r["device_type"] == "pc"


def test_registry_rejects_an_unknown_tool():
    import pytest
    with pytest.raises(ValueError):
        DeviceAPIRegistry().call("definitely_not_a_tool")


def test_every_tool_defaults_to_phone_when_device_type_is_omitted():
    """Backwards compatibility: an older call with no device_type must still work rather than
    raising, since the model can omit an optional argument."""
    assert lookup_manual("wifi")["device_type"] == "phone"
    assert get_device_status()["device_type"] == "phone"
    assert open_settings("Wi-Fi")["device_type"] == "phone"
    assert reset_network_settings()["device_type"] == "phone"
