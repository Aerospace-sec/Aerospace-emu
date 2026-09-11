from sim.usb_lab import UsbBoundary, UsbDevice, UsbPort, run_scenarios


def test_usb_requires_approved_signed_device():
    boundary = UsbBoundary((UsbPort("p", "MAINTENANCE", "maintenance_data"),), approved_devices=("ok",))
    device = UsbDevice("v", "p", "ok", "mass_storage", "1", True, "d")
    assert boundary.enumerate("p", device).accepted
    unsigned = UsbDevice("v", "p", "ok", "mass_storage", "1", False, "d")
    assert boundary.enumerate("p", unsigned).reason == "firmware_signature"

def test_usb_negative_boundaries_isolate_or_charge_only():
    attacks = run_scenarios()["attacks"]
    assert attacks["unknown_device"]["action"] == "isolate"
    assert attacks["cabin_data"]["reason"] == "cabin_data_disabled"
    assert attacks["cabin_data"]["action"] == "charge_only"
    assert attacks["class_spoof"]["reason"] == "device_class_policy"
    assert attacks["oversize_transfer"]["reason"] == "transfer_limit"
