#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
project_dir=${PETALINUX_PROJECT_DIR:-"${repo_root}/software/petalinux/cora-z7-10-baseline"}
xsa=${PETALINUX_XSA:-"${repo_root}/hardware/export/cora-z7-10/cora_z7_10_base.xsa"}

if [[ -z ${PETALINUX:-} || ${PETALINUX_VER:-} != 2025.1 ]]; then
    printf 'ERROR: activate PetaLinux 2025.1 first:\n' >&2
    printf '  source scripts/activate-tools.sh\n' >&2
    exit 1
fi

if [[ ! -r $xsa ]]; then
    printf 'ERROR: XSA not found: %s\n' "$xsa" >&2
    exit 1
fi

if [[ -e $project_dir ]]; then
    printf 'ERROR: project already exists: %s\n' "$project_dir" >&2
    printf 'Refusing to overwrite it.\n' >&2
    exit 1
fi

mkdir -p "$(dirname "$project_dir")"
cd "$(dirname "$project_dir")"
petalinux-create project --template zynq --name "$(basename "$project_dir")"
petalinux-config --project "$project_dir" --get-hw-description "$xsa" --silentconfig

printf 'Created PetaLinux baseline at %s\n' "$project_dir"
