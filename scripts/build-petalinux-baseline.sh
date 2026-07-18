#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
project_dir=${PETALINUX_PROJECT_DIR:-"${repo_root}/software/petalinux/cora-z7-10-baseline"}

if [[ -z ${PETALINUX:-} || ${PETALINUX_VER:-} != 2025.1 ]]; then
    printf 'ERROR: activate PetaLinux 2025.1 first:\n' >&2
    printf '  source scripts/activate-tools.sh\n' >&2
    exit 1
fi

if [[ ! -r ${project_dir}/project-spec/configs/config ]]; then
    printf 'ERROR: configured PetaLinux project not found: %s\n' "$project_dir" >&2
    exit 1
fi

petalinux-build --project "$project_dir"
