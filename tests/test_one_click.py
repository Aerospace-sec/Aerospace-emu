import subprocess
import sys


def test_one_click_dry_run_lists_all_simulation_stages():
    completed = subprocess.run(
        [sys.executable, "scripts/run_all_simulations.py", "--profile", "quick", "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    for stage in ("arinc429", "afdx", "arinc825", "maintenance-network", "usb", "serial", "atg5g", "civil-platform", "virtual-hardware", "chain-normal", "chain-injection", "render-reports"):
        assert f"[{stage}]" in completed.stdout

def test_one_click_rejects_unknown_profile():
    completed = subprocess.run(
        [sys.executable, "scripts/run_all_simulations.py", "--profile", "unsupported", "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2
