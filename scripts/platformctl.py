#!/usr/bin/env python3
"""Inspect, validate, snapshot, and bootstrap unified hardware platforms."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLATFORMS_ROOT = REPOSITORY_ROOT / "platforms"
PLATFORM_ID_RE = re.compile(r"^0x[0-9a-fA-F]{8}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PlatformError(RuntimeError):
    pass


def platform_files() -> list[Path]:
    return sorted(PLATFORMS_ROOT.glob("*/platform.json"))


def load_platform(platform_id: str) -> tuple[Path, dict[str, Any]]:
    path = PLATFORMS_ROOT / platform_id / "platform.json"
    if not path.is_file():
        known = ", ".join(item.parent.name for item in platform_files()) or "none"
        raise PlatformError(f"unknown platform {platform_id!r}; available: {known}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlatformError(f"cannot load {path}: {error}") from error
    return path, data


def repository_path(value: str) -> Path:
    path = (REPOSITORY_ROOT / value).resolve()
    try:
        path.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise PlatformError(f"path escapes repository: {value}") from error
    return path


def validate_platform(path: Path, data: dict[str, Any], *, strict: bool) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "id", "display_name", "status", "platform_id",
        "soc", "board", "hardware", "linux", "software", "deployment", "snapshot",
    }
    missing = sorted(required - data.keys())
    if missing:
        return [f"missing top-level fields: {', '.join(missing)}"]
    unexpected = sorted(data.keys() - required)
    if unexpected:
        errors.append(f"unexpected top-level fields: {', '.join(unexpected)}")
    if data["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if not isinstance(data["id"], str) or data["id"] != path.parent.name or not NAME_RE.fullmatch(data["id"]):
        errors.append("id must be hyphen-case and match its platform directory")
    if not isinstance(data["status"], str) or data["status"] not in {"validated", "development", "scaffold", "retired"}:
        errors.append("status is invalid")
    if not isinstance(data["platform_id"], str) or not PLATFORM_ID_RE.fullmatch(data["platform_id"]):
        errors.append("platform_id must be an eight-digit hexadecimal string")

    section_fields = {
        "soc": {"family", "device", "architecture", "petalinux_template"},
        "board": {"module", "carrier", "revision", "board_part"},
        "hardware": {"build_script", "xsa", "bitstream", "platform_overlay"},
        "linux": {"project", "platform_overlay", "shared_layers", "image", "init_system"},
        "software": {"common_components", "required_capabilities"},
        "deployment": {"hostname", "ssh_user", "addresses"},
        "snapshot": {"include", "live_commands"},
    }
    sections: dict[str, dict[str, Any]] = {}
    for name, expected in section_fields.items():
        value = data.get(name)
        if not isinstance(value, dict):
            errors.append(f"{name} must be an object")
            sections[name] = {}
            continue
        sections[name] = value
        missing_fields = sorted(expected - value.keys())
        extra_fields = sorted(value.keys() - expected)
        if missing_fields:
            errors.append(f"{name} is missing fields: {', '.join(missing_fields)}")
        if extra_fields:
            errors.append(f"{name} has unexpected fields: {', '.join(extra_fields)}")

    soc = sections["soc"]
    expected_templates = {
        "zynq-7000": ("armv7a", "zynq"),
        "zynq-ultrascale-plus": ("aarch64", "zynqMP"),
        "versal": ("aarch64", "versal"),
    }
    family = soc.get("family")
    if family not in expected_templates:
        errors.append("soc.family is invalid")
    elif (soc.get("architecture"), soc.get("petalinux_template")) != expected_templates[family]:
        errors.append(f"architecture/template do not match {family}")

    hardware = sections["hardware"]
    linux = sections["linux"]
    path_fields: list[tuple[str, Any, bool]] = [
        ("hardware.build_script", hardware.get("build_script"), False),
        ("hardware.xsa", hardware.get("xsa"), False),
        ("hardware.bitstream", hardware.get("bitstream"), False),
        ("hardware.platform_overlay", hardware.get("platform_overlay"), True),
        ("linux.project", linux.get("project"), False),
        ("linux.platform_overlay", linux.get("platform_overlay"), True),
        ("linux.image", linux.get("image"), False),
    ]
    shared_layers = linux.get("shared_layers", [])
    if not isinstance(shared_layers, list):
        errors.append("linux.shared_layers must be an array")
        shared_layers = []
    elif len(shared_layers) != len(set(item for item in shared_layers if isinstance(item, str))):
        errors.append("linux.shared_layers must not contain duplicates")
    for index, layer in enumerate(shared_layers):
        path_fields.append((f"linux.shared_layers[{index}]", layer, True))
    for label, value, strict_required in path_fields:
        if value is None and label in {"hardware.build_script", "hardware.bitstream"}:
            continue
        if not isinstance(value, str):
            errors.append(f"{label} must be a repository-relative path")
            continue
        try:
            resolved = repository_path(value)
        except PlatformError as error:
            errors.append(str(error))
            continue
        if strict and strict_required and not resolved.exists():
            errors.append(f"{label} does not exist: {value}")

    for label in ("common_components", "required_capabilities"):
        values = sections["software"].get(label)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not NAME_RE.fullmatch(item) for item in values
        ):
            errors.append(f"software.{label} must be an array of hyphen-case names")
        elif len(values) != len(set(values)):
            errors.append(f"software.{label} must not contain duplicates")

    includes = sections["snapshot"].get("include")
    if not isinstance(includes, list) or not includes:
        errors.append("snapshot.include must be a non-empty array")
    else:
        for pattern in includes:
            if not isinstance(pattern, str) or not pattern:
                errors.append("snapshot.include entries must be non-empty strings")
            elif Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                errors.append(f"snapshot.include must remain inside the repository: {pattern}")

    if strict and data["status"] == "validated":
        for label, value in (
            ("hardware.build_script", hardware.get("build_script")),
            ("hardware.xsa", hardware.get("xsa")),
            ("hardware.bitstream", hardware.get("bitstream")),
            ("linux.project", linux.get("project")),
            ("linux.image", linux.get("image")),
        ):
            if not isinstance(value, str) or not value or not repository_path(value).exists():
                errors.append(f"validated platform requires existing {label}: {value}")
    return errors


def command_list(_: argparse.Namespace) -> None:
    for path in platform_files():
        _, data = load_platform(path.parent.name)
        print(f"{data['id']:<24} {data['status']:<12} {data['soc']['architecture']:<8} {data['display_name']}")


def command_show(args: argparse.Namespace) -> None:
    _, data = load_platform(args.platform)
    print(json.dumps(data, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    selected = [load_platform(args.platform)] if args.platform else [load_platform(p.parent.name) for p in platform_files()]
    registry = [load_platform(path.parent.name) for path in platform_files()]
    id_counts: dict[Any, int] = {}
    numeric_id_counts: dict[Any, int] = {}
    for _, item in registry:
        textual = item.get("id")
        numeric = item.get("platform_id")
        if isinstance(textual, str):
            id_counts[textual] = id_counts.get(textual, 0) + 1
        if isinstance(numeric, str):
            numeric_id_counts[numeric] = numeric_id_counts.get(numeric, 0) + 1
    failed = False
    for path, data in selected:
        errors = validate_platform(path, data, strict=args.strict)
        if id_counts.get(data.get("id"), 0) > 1:
            errors.append("duplicate platform id in registry")
        if numeric_id_counts.get(data.get("platform_id"), 0) > 1:
            errors.append("duplicate numeric platform_id in registry")
        if errors:
            failed = True
            print(f"FAIL {path.relative_to(REPOSITORY_ROOT)}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"OK   {path.relative_to(REPOSITORY_ROOT)}")
    if failed:
        raise SystemExit(1)


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=REPOSITORY_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_inputs(patterns: list[str]) -> list[dict[str, Any]]:
    matches: dict[str, Path] = {}
    excluded_names = {
        ".git", ".Xil", "build", "images", "components", ".petalinux",
        "__pycache__", "snapshots", "downloads", "sstate-cache", "third_party",
    }
    excluded_suffixes = (".cache", ".gen", ".hw", ".ip_user_files", ".runs", ".sim")
    for root, directories, filenames in os.walk(REPOSITORY_ROOT):
        directories[:] = [
            name for name in directories
            if name not in excluded_names and not name.endswith(excluded_suffixes)
        ]
        root_path = Path(root)
        for filename in filenames:
            if filename.lower() in {"token", "credentials", "credentials.json", "auth.json"}:
                continue
            path = root_path / filename
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                matches[relative] = path
    return [
        {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}
        for relative, path in sorted(matches.items())
    ]


def write_snapshot_archive(path: Path, inputs: list[dict[str, Any]]) -> None:
    with path.open("wb") as raw_stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="w") as archive:
                for item in inputs:
                    source = repository_path(item["path"])
                    info = archive.gettarinfo(str(source), arcname=item["path"])
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)


def live_snapshot(data: dict[str, Any], ssh_key: Path | None) -> dict[str, Any]:
    addresses = data["deployment"]["addresses"]
    user = data["deployment"]["ssh_user"]
    if not addresses or not user:
        raise PlatformError("live snapshot requires deployment.ssh_user and at least one address")
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=no",
    ]
    if ssh_key:
        ssh += ["-i", str(ssh_key), "-o", "IdentitiesOnly=yes"]
    target = f"{user}@{addresses[0]}"
    results: dict[str, Any] = {"target": target, "commands": {}}
    for item in data["snapshot"]["live_commands"]:
        completed = subprocess.run(
            [*ssh, target, item["command"]], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        results["commands"][item["name"]] = {
            "returncode": completed.returncode,
            "output": completed.stdout.rstrip("\n"),
        }
    return results


def command_snapshot(args: argparse.Namespace) -> None:
    path, data = load_platform(args.platform)
    errors = validate_platform(path, data, strict=False)
    if errors:
        raise PlatformError("invalid platform: " + "; ".join(errors))
    timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    label = re.sub(r"[^a-z0-9-]+", "-", args.label.lower()).strip("-") if args.label else "snapshot"
    output_dir = path.parent / "snapshots"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{stamp}-{label}.json"
    archive = output.with_suffix(".tar.gz")
    inputs = snapshot_inputs(data["snapshot"]["include"])
    write_snapshot_archive(archive, inputs)
    record: dict[str, Any] = {
        "snapshot_version": 1,
        "created_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "platform": data["id"],
        "platform_definition_sha256": sha256(path),
        "source": {
            "branch": git_output("branch", "--show-current"),
            "commit": git_output("rev-parse", "HEAD"),
            "dirty": bool(git_output("status", "--porcelain")),
            "status": git_output("status", "--short").splitlines(),
        },
        "inputs": inputs,
        "source_archive": {
            "path": archive.relative_to(REPOSITORY_ROOT).as_posix(),
            "sha256": sha256(archive),
        },
    }
    if args.live:
        record["live"] = live_snapshot(data, args.ssh_key)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(output.relative_to(REPOSITORY_ROOT))
    print(f"inputs={len(record['inputs'])} manifest_sha256={sha256(output)}")
    print(f"archive={archive.relative_to(REPOSITORY_ROOT)} sha256={record['source_archive']['sha256']}")


def replace_config_value(config: Path, key: str, value: str) -> None:
    text = config.read_text(encoding="utf-8")
    expression = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f'{key}="{value}"'
    if expression.search(text):
        text = expression.sub(replacement, text)
    else:
        text += f"\n{replacement}\n"
    config.write_text(text, encoding="utf-8")


def command_bootstrap(args: argparse.Namespace) -> None:
    _, data = load_platform(args.platform)
    project = repository_path(data["linux"]["project"])
    xsa = repository_path(data["hardware"]["xsa"])
    overlay = repository_path(data["linux"]["platform_overlay"])
    if project.exists():
        raise PlatformError(f"refusing to overwrite existing project: {project}")
    if not xsa.is_file():
        raise PlatformError(f"hardware XSA is missing: {xsa}")
    if not overlay.is_dir():
        raise PlatformError(f"platform PetaLinux overlay is missing: {overlay}")
    if os.environ.get("PETALINUX_VER") != "2025.1":
        raise PlatformError("activate PetaLinux 2025.1 with: source scripts/activate-tools.sh")

    project.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "petalinux-create", "project", "--template", data["soc"]["petalinux_template"],
        "--name", project.name,
    ], cwd=project.parent, check=True)
    subprocess.run([
        "petalinux-config", "--project", str(project),
        "--get-hw-description", str(xsa), "--silentconfig",
    ], cwd=REPOSITORY_ROOT, check=True)
    shutil.copytree(overlay, project / "project-spec" / "meta-user", dirs_exist_ok=True)

    config = project / "project-spec" / "configs" / "config"
    for index, layer in enumerate(data["linux"]["shared_layers"]):
        relative_layer = os.path.relpath(repository_path(layer), project)
        replace_config_value(
            config, f"CONFIG_USER_LAYER_{index}", f"${{PROOT}}/{relative_layer}"
        )
    subprocess.run([
        "petalinux-config", "--project", str(project), "--silentconfig",
    ], cwd=REPOSITORY_ROOT, check=True)
    print(f"created {project.relative_to(REPOSITORY_ROOT)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="list known platforms")
    list_parser.set_defaults(func=command_list)
    show_parser = commands.add_parser("show", help="print one platform definition")
    show_parser.add_argument("platform")
    show_parser.set_defaults(func=command_show)
    validate_parser = commands.add_parser("validate", help="validate platform definitions")
    validate_parser.add_argument("platform", nargs="?")
    validate_parser.add_argument("--strict", action="store_true", help="require all active input paths")
    validate_parser.set_defaults(func=command_validate)
    snapshot_parser = commands.add_parser("snapshot", help="record source and optional live-board state")
    snapshot_parser.add_argument("platform")
    snapshot_parser.add_argument("--label", default="snapshot")
    snapshot_parser.add_argument("--live", action="store_true")
    snapshot_parser.add_argument("--ssh-key", type=Path)
    snapshot_parser.set_defaults(func=command_snapshot)
    bootstrap_parser = commands.add_parser("bootstrap", help="create a PetaLinux project from its XSA")
    bootstrap_parser.add_argument("platform")
    bootstrap_parser.set_defaults(func=command_bootstrap)
    return parser


def main() -> None:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except PlatformError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
