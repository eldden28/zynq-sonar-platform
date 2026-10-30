"""Maintained platform names and native EDF hardware-import parameters."""
import os

PLATFORMS = {
    "cora-z7-10": {
        "soc": "zynq", "init_prefix": "ps7", "hardware_conf": "cora-hardware.conf",
        "generated_machine": "zynq-cora-z7-10-sdt-full", "machine": "cora-z7-10-edf",
    },
    "ultrazed-3eg-iocc": {
        "soc": "zynqmp", "init_prefix": "psu", "hardware_conf": "ultrazed-hardware.conf",
        "generated_machine": "zynqmp-ultrazed-3eg-iocc-sdt-full", "machine": "ultrazed-3eg-iocc-edf",
    },
}


def select_platform(workspace, requested=None):
    marker = workspace / ".edf-platform"
    saved = marker.read_text().strip() if marker.exists() else None
    name = requested or os.environ.get("EDF_PLATFORM") or saved or "cora-z7-10"
    if name not in PLATFORMS:
        raise ValueError(f"Unsupported EDF platform: {name}")
    if saved and saved != name:
        raise ValueError(f"Workspace is configured for {saved}; use a separate workspace for {name}")
    return name, PLATFORMS[name]
