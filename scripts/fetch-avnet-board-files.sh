#!/usr/bin/env bash

set -euo pipefail

readonly REPOSITORY_URL=https://github.com/Avnet/bdf.git
readonly REPOSITORY_COMMIT=e9723b55a68b106fb4cdb645974d096cd3594a2a

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)
destination="$repository_root/third_party/avnet-vivado-boards"
fresh_clone=0

if [[ -e $destination && ! -d $destination/.git ]]; then
    printf 'ERROR: destination exists but is not the expected Git repository: %s\n' \
        "$destination" >&2
    exit 1
fi

if [[ ! -d $destination/.git ]]; then
    mkdir -p "$(dirname -- "$destination")"
    git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$destination"
    fresh_clone=1
fi

actual_url=$(git -C "$destination" remote get-url origin)
if [[ $actual_url != "$REPOSITORY_URL" ]]; then
    printf 'ERROR: unexpected origin for %s: %s\n' "$destination" "$actual_url" >&2
    exit 1
fi

if ! git -C "$destination" cat-file -e "${REPOSITORY_COMMIT}^{commit}" 2>/dev/null; then
    git -C "$destination" fetch --depth 1 origin "$REPOSITORY_COMMIT"
fi

if [[ $fresh_clone -eq 0 && -n $(git -C "$destination" status --porcelain) ]]; then
    printf 'ERROR: refusing to replace local changes in %s\n' "$destination" >&2
    exit 1
fi

git -C "$destination" checkout --detach "$REPOSITORY_COMMIT"

board_dir="$destination/ultrazed_3eg_pciecc/1.4"
for required_file in board.xml preset.xml part0_pins.xml; do
    if [[ ! -r $board_dir/$required_file ]]; then
        printf 'ERROR: required UltraZed board file is missing: %s\n' \
            "$board_dir/$required_file" >&2
        exit 1
    fi
done

printf 'Avnet board files ready at commit %s\n' "$REPOSITORY_COMMIT"
printf 'Vivado board repository: %s\n' "$destination"
