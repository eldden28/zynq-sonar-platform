#!/usr/bin/env python3
"""Import a Cora 2025.1 XSA through SDT into an independent EDF build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xsa", type=Path)
    parser.add_argument("--sdtgen", type=Path, default=Path("/tools/Xilinx/2025.1/Vitis/bin/sdtgen"))
    parser.add_argument("--workspace", type=Path, default=ROOT / "software/edf/workspace")
    parser.add_argument("--sdt-only", action="store_true", help="Generate SDT without starting native BitBake tools")
    args = parser.parse_args()
    if sys.platform != "linux" or os.environ.get("PETALINUX") or os.geteuid() == 0:
        parser.error("Run on Linux as a normal user in a fresh shell without PetaLinux")
    workspace = args.workspace.resolve()
    xsa = args.xsa.resolve(strict=True)
    sdt = workspace / "hardware/cora-z7-10-sdt"
    provenance = workspace / "hardware/cora-z7-10.json"
    digest = hashlib.sha256(xsa.read_bytes()).hexdigest()
    if not (sdt / "system-top.dts").is_file():
        if sdt.exists() and any(sdt.iterdir()):
            parser.error(f"Incomplete SDT directory: {sdt}; move it aside before retrying")
        sdt.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(args.sdtgen), str(ROOT / "software/edf/generate-sdt.tcl"), str(xsa), str(sdt)], check=True)
    elif not provenance.exists() or json.loads(provenance.read_text())["xsa_sha256"] != digest:
        parser.error(f"Existing SDT has no matching XSA provenance: {sdt}; move it aside before importing")
    provenance.write_text(json.dumps({"xsa": str(xsa), "xsa_sha256": digest,
                                     "sdtgen": str(args.sdtgen.resolve()), "release": "2025.1"}, indent=2) + "\n")
    for name in ["system-top.dts", "ps7_init.c", "ps7_init.h"]:
        if not (sdt / name).is_file():
            parser.error(f"SDT generation did not produce {name}")
    bitstreams = list(sdt.glob("*.bit"))
    if len(bitstreams) != 1:
        parser.error("Expected exactly one bitstream in the SDT output; export an XSA with bitstream")
    if args.sdt_only:
        print(f"SDT generated and recorded: {sdt}")
        return
    if not os.environ.get("BUILDDIR") or Path(os.environ["BUILDDIR"]).resolve() != workspace / "build":
        parser.error("Source scripts/activate-edf.sh in this workspace before importing the machine")
    gen = shutil.which("gen-machineconf")
    if not gen:
        parser.error("gen-machineconf is unavailable; source scripts/activate-edf.sh")
    hardware_conf = workspace / "build/conf/cora-hardware.conf"
    # A successful previous import is kept intact; source changes require an
    # explicit new hardware workspace, so stale and new machine files cannot mix.
    machine = workspace / "build/conf/machine/zynq-cora-z7-10-sdt-full.conf"
    if machine.exists():
        parser.error(f"Machine already imported: {machine}")
    subprocess.run([gen, "parse-sdt", "--hw-description", str(sdt),
                    "--soc-family", "zynq", "--machine-name", "zynq-cora-z7-10-sdt-full",
                    "--config-dir", str(workspace / "build/conf"),
                    "--output", str(workspace / "generated/cora-z7-10"),
                    "--pl", str(bitstreams[0]), "--localconf", str(hardware_conf)], check=True)
    with hardware_conf.open("a") as config:
        config.write('\n# Apply maintained Cora board settings to the generated SDT machine.\nMACHINE = "cora-z7-10-edf"\n')
    print(f"Imported Cora machine: {machine}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
