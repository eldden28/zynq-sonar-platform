#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
destination="${repo_root}/third_party/meta-sdr"
revision=563727209e5d8c7cdebc35157bab4dc07e8fb235
url=https://github.com/balister/meta-sdr.git

if [[ ! -d ${destination}/.git ]]; then
    git clone --branch master "${url}" "${destination}"
fi

git -C "${destination}" fetch --no-filter --force origin \
    +refs/heads/master:refs/remotes/origin/master
if [ "$(git -C "${destination}" rev-parse HEAD)" != "${revision}" ]; then
    git -C "${destination}" checkout --detach "${revision}"
fi

# The pinned revision targets newer OE series, but its GNU Radio and VOLK
# recipes use metadata syntax supported by PetaLinux 2025.1 (Scarthgap).
if ! grep -q 'scarthgap' "${destination}/conf/layer.conf"; then
    sed -i 's/LAYERSERIES_COMPAT_sdr-layer = "/LAYERSERIES_COMPAT_sdr-layer = "scarthgap /' \
        "${destination}/conf/layer.conf"
fi

# cmake_qt5 is inherited while the base recipe is parsed, before a bbappend
# can replace PACKAGECONFIG. Change the upstream default at fetch time so a
# headless build never adds Qt, GRC, or ZeroMQ to the dependency graph.
gnuradio_recipe="${destination}/recipes-core/gnuradio/gnuradio_git.bb"
sed -i 's/PACKAGECONFIG ??= "qtgui5 grc zeromq"/PACKAGECONFIG ??= ""/' \
    "${gnuradio_recipe}"
if grep -q 'PACKAGECONFIG ??= "qtgui5 grc zeromq"' "${gnuradio_recipe}"; then
    printf 'ERROR: failed to select the headless GNU Radio configuration\n' >&2
    exit 1
fi

actual=$(git -C "${destination}" rev-parse HEAD)
if [ "${actual}" != "${revision}" ]; then
    printf 'ERROR: meta-sdr revision mismatch: %s\n' "${actual}" >&2
    exit 1
fi

printf 'meta-sdr ready at %s (%s)\n' "${destination}" "${revision}"
