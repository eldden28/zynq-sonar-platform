#!/usr/bin/env bash

set -euo pipefail

readonly REPOSITORY_URL=https://github.com/Digilent/vivado-boards.git
readonly REPOSITORY_COMMIT=36f34ab687b7fa9c778b779d027f3bce63b3ace9

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)
destination="$repository_root/third_party/digilent-vivado-boards"

if [[ -e $destination && ! -d $destination/.git ]]; then
    printf 'ERROR: destination exists but is not the expected Git repository: %s\n' \
        "$destination" >&2
    exit 1
fi

if [[ ! -d $destination/.git ]]; then
    mkdir -p "$(dirname -- "$destination")"
    git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$destination"
fi

actual_url=$(git -C "$destination" remote get-url origin)
if [[ $actual_url != "$REPOSITORY_URL" ]]; then
    printf 'ERROR: unexpected origin for %s: %s\n' "$destination" "$actual_url" >&2
    exit 1
fi

if ! git -C "$destination" cat-file -e "${REPOSITORY_COMMIT}^{commit}" 2>/dev/null; then
    git -C "$destination" fetch --depth 1 origin "$REPOSITORY_COMMIT"
fi

if [[ -n $(git -C "$destination" status --porcelain) ]]; then
    printf 'ERROR: refusing to replace local changes in %s\n' "$destination" >&2
    exit 1
fi

git -C "$destination" checkout --detach "$REPOSITORY_COMMIT"

board_dir="$destination/new/board_files/cora-z7-10/B.0"
for required_file in board.xml preset.xml part0_pins.xml; do
    if [[ ! -r $board_dir/$required_file ]]; then
        printf 'ERROR: required Cora Z7-10 board file is missing: %s\n' \
            "$board_dir/$required_file" >&2
        exit 1
    fi
done

printf 'Digilent board files ready at commit %s\n' "$REPOSITORY_COMMIT"
printf 'Vivado board repository: %s\n' "$destination/new/board_files"

