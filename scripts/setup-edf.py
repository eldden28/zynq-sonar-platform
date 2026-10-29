#!/usr/bin/env python3
"""Fetch the release-pinned EDF layers without a PetaLinux environment."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "software/edf/manifests/amd-edf-rel-v25.05.xml"
SDR_REVISION = "563727209e5d8c7cdebc35157bab4dc07e8fb235"


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def fetch(url, revision, path, submodules=False):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(f"Unpinned revision: {revision}")
    if not (path / ".git").exists():
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"Refusing to overwrite {path}")
        path.mkdir(parents=True, exist_ok=True)
        run("git", "init", "-q", str(path))
        run("git", "remote", "add", "origin", url, cwd=path)
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=path, text=True).strip()
    if remote != url:
        raise ValueError(f"Unexpected origin for {path}: {remote}")
    current = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=path, capture_output=True, text=True)
    if current.stdout.strip() != revision:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=path):
            raise ValueError(f"Refusing to switch modified source tree: {path}")
        run("git", "fetch", "--depth=1", "origin", revision, cwd=path)
        run("git", "checkout", "--detach", revision, cwd=path)
    if submodules:
        run("git", "submodule", "update", "--init", "--recursive", "--depth=1", cwd=path)
    print(f"Pinned {path.name}: {revision}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT / "software/edf/workspace")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("Run as a normal user on Linux")
    if os.environ.get("PETALINUX") or os.environ.get("OECORE_NATIVE_SYSROOT"):
        parser.error("Start a fresh shell without a PetaLinux or SDK environment")
    workspace = args.workspace.resolve()
    manifest = ET.parse(MANIFEST).getroot()
    remotes = {r.attrib["name"]: r.attrib["fetch"] for r in manifest.findall("remote")}
    for project in manifest.findall("project"):
        data = project.attrib
        destination = workspace / data["path"]
        destination.resolve().relative_to(workspace)
        fetch(f'{remotes[data["remote"]]}/{data["name"]}.git', data["revision"], destination,
              data.get("sync-s") == "true")
        for item in project.findall("copyfile"):
            target = workspace / item.attrib["dest"]
            target.resolve().relative_to(workspace)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination / item.attrib["src"], target)
    sdr = workspace / "sources/meta-sdr"
    fetch("https://github.com/balister/meta-sdr.git", SDR_REVISION, sdr)
    # Same explicit Scarthgap/headless adaptation as fetch-meta-sdr.sh.
    layer = sdr / "conf/layer.conf"
    text = layer.read_text()
    if "scarthgap" not in text:
        layer.write_text(text.replace('LAYERSERIES_COMPAT_sdr-layer = "',
                                     'LAYERSERIES_COMPAT_sdr-layer = "scarthgap ', 1))
    recipe = sdr / "recipes-core/gnuradio/gnuradio_git.bb"
    text = recipe.read_text()
    if 'PACKAGECONFIG ??= "qtgui5 grc zeromq"' in text:
        recipe.write_text(text.replace('PACKAGECONFIG ??= "qtgui5 grc zeromq"', 'PACKAGECONFIG ??= ""', 1))
    elif 'PACKAGECONFIG ??= ""' not in text:
        raise ValueError("Unexpected GNU Radio recipe; cannot select headless configuration")
    print(f"EDF sources ready: {workspace}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
