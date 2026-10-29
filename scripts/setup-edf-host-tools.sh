#!/usr/bin/env bash
# Optional rootless Ubuntu 22.04 host utilities. No system packages are changed.
set -euo pipefail
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 22.04 || $(uname -m) != x86_64 ]]; then
    printf 'This rootless package helper is for Ubuntu 22.04 x86-64 only.\n' >&2
    exit 1
fi
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
workspace=${EDF_WORKSPACE:-"$repo_root/software/edf/workspace"}
tools="$workspace/host-tools"
mkdir -p "$tools/packages" "$tools/bin"
cd "$tools/packages"
apt-get download gawk chrpath diffstat socat libsigsegv2 libwrap0 lz4 \
    python3-jinja2 python3-markupsafe python3-git python3-gitdb python3-smmap \
    python3-subunit python3-yaml device-tree-compiler libfdt1 mtools
for package in ./*.deb; do
    dpkg-deb -x "$package" "$tools"
done
# Keep the local libraries scoped to these two executables. Exporting a global
# LD_LIBRARY_PATH can break Yocto's compiler and native-tool isolation.
for tool in gawk socat dtc; do
    cat > "$tools/bin/$tool" <<EOF
#!/usr/bin/env bash
tool_root=\$(cd "\$(dirname "\$(readlink -f "\${BASH_SOURCE[0]}")")/.." && pwd)
exec /lib64/ld-linux-x86-64.so.2 --library-path "\$tool_root/usr/lib/x86_64-linux-gnu" "\$tool_root/usr/bin/$tool" "\$@"
EOF
    chmod +x "$tools/bin/$tool"
done
printf 'Local host tools ready. Activate with:\n  EDF_HOST_TOOLS=%q source scripts/activate-edf.sh\n' "$tools"
