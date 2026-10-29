#!/usr/bin/env bash
# Source this from a fresh shell; EDF must not inherit a PetaLinux SDK.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    printf 'Source this script: source scripts/activate-edf.sh\n' >&2
    exit 1
fi
if [[ -n ${PETALINUX:-} || -n ${OECORE_NATIVE_SYSROOT:-} ]]; then
    printf 'Start a fresh shell without PetaLinux or an SDK environment.\n' >&2
    return 1
fi
_edf_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
_edf_workspace=${EDF_WORKSPACE:-"$_edf_repo/software/edf/workspace"}
if [[ -n ${EDF_HOST_TOOLS:-} ]]; then
    PATH="$EDF_HOST_TOOLS/bin:$EDF_HOST_TOOLS/usr/bin:$PATH"
    PYTHONPATH="$EDF_HOST_TOOLS/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
    export PATH PYTHONPATH
fi
if ! python3 "$_edf_repo/scripts/configure-edf.py" --workspace "$_edf_workspace"; then
    unset _edf_repo _edf_workspace
    return 1
fi
export PYTHONDONTWRITEBYTECODE=1
export PATH="$_edf_workspace/sources/meta-xilinx/meta-xilinx-core/gen-machine-conf:$PATH"
source "$_edf_workspace/sources/poky/oe-init-build-env" "$_edf_workspace/build"
_edf_status=$?
unset _edf_repo _edf_workspace
if [[ $_edf_status -ne 0 ]]; then
    unset _edf_status
    return 1
fi
unset _edf_status
