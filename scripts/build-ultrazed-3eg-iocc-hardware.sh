#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export ULTRAZED_PLATFORM=ultrazed-3eg-iocc
exec "$script_dir/build-ultrazed-3eg-pcie-hardware.sh" "$@"
