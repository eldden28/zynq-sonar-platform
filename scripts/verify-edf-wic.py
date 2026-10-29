#!/usr/bin/env python3
"""Inspect a completed sonar WIC without mounting it or accessing a board."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_image(data, expected_type):
    if len(data) < 64:
        raise ValueError("Truncated legacy U-Boot image")
    magic, header_crc, _, size, load, entry, data_crc, _, arch, kind, _, _ = struct.unpack(
        ">7I4B32s", data[:64])
    header = data[:4] + bytes(4) + data[8:64]
    if magic != 0x27051956 or kind != expected_type or arch != 2:
        raise ValueError("Unexpected U-Boot image magic, type, or CPU architecture")
    if zlib.crc32(header) != header_crc or len(data) < 64 + size or zlib.crc32(data[64:64 + size]) != data_crc:
        raise ValueError("U-Boot image size or CRC check failed")
    return {"payload_bytes": size, "load": hex(load), "entry": hex(entry), "type": kind}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wic", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    wic = args.wic.resolve(strict=True)
    mcopy = shutil.which("mcopy")
    debugfs = shutil.which("debugfs") or "/usr/sbin/debugfs"
    if not mcopy or not Path(debugfs).is_file():
        parser.error("Install mtools and e2fsprogs (or activate the local EDF host tools)")
    with wic.open("rb") as image:
        mbr = image.read(512)
        if len(mbr) != 512 or mbr[510:] != bytes.fromhex("55aa"):
            raise ValueError("Missing MBR signature")
        parts = []
        for number in range(4):
            active, _, kind, _, start, sectors = struct.unpack("<B3sB3sII", mbr[446 + 16 * number:462 + 16 * number])
            if kind:
                parts.append({"number": number + 1, "type": hex(kind), "active": active == 0x80,
                              "start_sector": start, "sectors": sectors})
        if len(parts) != 2 or parts[0]["type"] not in ["0x6", "0xb", "0xc", "0xe"] or parts[1]["type"] != "0x83":
            raise ValueError(f"Expected FAT boot and Linux rootfs partitions: {parts}")
        if not parts[0]["active"]:
            raise ValueError("Boot partition is not active")
        for part in parts:
            if not part["sectors"] or (part["start_sector"] + part["sectors"]) * 512 > wic.stat().st_size:
                raise ValueError("Partition extends outside the WIC")
        if parts[0]["start_sector"] + parts[0]["sectors"] > parts[1]["start_sector"]:
            raise ValueError("Overlapping partitions")
        image.seek(parts[1]["start_sector"] * 512 + 1024 + 56)
        if image.read(2) != bytes.fromhex("53ef"):
            raise ValueError("Linux partition has no ext4 superblock")
    required_packages = {"cora-dashboard", "cora-sonar", "cora-triplet-beamformer", "cora-navigation", "cora-pwm", "cora-dsp", "cora-ofdm-acoustic", "python3-numpy", "os-release"}
    packages = {line.split()[0] for line in args.manifest.read_text().splitlines() if line.split()}
    if required_packages - packages:
        raise ValueError(f"Missing sonar packages: {sorted(required_packages - packages)}")
    module_packages = {}
    for driver in ["cora-pwm", "cora-dsp"]:
        matches = sorted(name for name in packages if name.startswith(f"kernel-module-{driver}-"))
        if len(matches) != 1:
            raise ValueError(f"Expected one versioned kernel module for {driver}: {matches}")
        module_packages[driver] = matches[0]
    report = {"wic": str(wic), "bytes": wic.stat().st_size, "sha256": sha256(wic),
              "partitions": parts, "sonar_packages": sorted(required_packages),
              "kernel_module_packages": module_packages, "boot_files": {}}
    with tempfile.TemporaryDirectory(prefix="sonar-wic-verify-") as temporary:
        temp = Path(temporary)
        for name in ["BOOT.BIN", "boot.scr", "uImage", "system.dtb"]:
            output = temp / name
            subprocess.run([mcopy, "-i", f'{wic}@@{parts[0]["start_sector"] * 512}', f"::/{name}", str(output)], check=True)
            data = output.read_bytes()
            if not data:
                raise ValueError(f"Empty boot artifact: {name}")
            checks = {"bytes": len(data), "sha256": sha256(output)}
            if name in ["uImage", "boot.scr"]:
                checks.update(legacy_image(data, 2 if name == "uImage" else 6))
                if name == "boot.scr":
                    script = data[72:].decode("utf-8")
                    if "@@" in script or "root=/dev/${bootdev}2 ro rootwait" not in script or "console=ttyPS0,115200" not in script:
                        raise ValueError("SD boot script lacks the Cora console/root partition settings")
                    checks["root_partition"] = 2
                    checks["serial_console"] = "ttyPS0,115200"
            elif name == "BOOT.BIN" and data[32:40] != bytes.fromhex("665599aa584e4c58"):
                raise ValueError("BOOT.BIN lacks the Zynq boot header")
            elif name == "system.dtb":
                magic, total = struct.unpack(">II", data[:8])
                if magic != 0xD00DFEED or total != len(data):
                    raise ValueError("Invalid device tree blob header")
                compatible = ["eldden28,cora-pwm-1.0", "eldden28,cora-dsp-2.0", "usb-nop-xceiv"]
                if any(value.encode() + b"\0" not in data for value in compatible):
                    raise ValueError("Device tree is missing the maintained Cora FPGA/USB policy")
                checks["cora_compatible"] = compatible
            report["boot_files"][name] = checks
        rootfs = temp / "rootfs.ext4"
        remaining = parts[1]["sectors"] * 512
        with wic.open("rb") as source, rootfs.open("wb") as target:
            source.seek(parts[1]["start_sector"] * 512)
            while remaining:
                block = source.read(min(4 * 1024 * 1024, remaining))
                if not block:
                    raise ValueError("Truncated rootfs partition")
                if block.strip(bytes(1)):
                    target.write(block)
                else:
                    target.seek(len(block), 1)
                remaining -= len(block)
            target.truncate(parts[1]["sectors"] * 512)
        required_files = ["/usr/sbin/cora-sonar", "/usr/sbin/cora-dashboard", "/usr/sbin/cora-navigation",
                          "/usr/bin/cora-triplet-beamformer", "/usr/bin/cora-ofdm-acoustic",
                          "/usr/share/cora-sonar/www/index.html", "/etc/init.d/cora-dashboard"]
        for driver, package in module_packages.items():
            kernel = package.removeprefix(f"kernel-module-{driver}-")
            module = driver.replace("-", "_")
            required_files += [f"/usr/lib/modules/{kernel}/updates/{module}.ko", f"/etc/modules-load.d/{module}.conf"]
        for filename in required_files:
            result = subprocess.run([debugfs, "-R", f"stat {filename}", str(rootfs)], capture_output=True, text=True, check=True)
            if "Inode:" not in result.stdout:
                raise ValueError(f"Missing file in rootfs partition: {filename}")
        report["rootfs_files"] = required_files
        report["os_release"] = subprocess.check_output([debugfs, "-R", "cat /usr/lib/os-release", str(rootfs)], stderr=subprocess.DEVNULL, text=True)
        if 'ID=sonar-edf' not in report["os_release"] and 'ID="sonar-edf"' not in report["os_release"]:
            raise ValueError("Rootfs does not identify the sonar EDF distribution")
    report_path = args.report or wic.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Verified sonar WIC: {wic}\nSHA-256: {report['sha256']}\nReport: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
