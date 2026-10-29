# EDF migration on Linux

Status: native source setup, Cora SDT generation, and initial BitBake validation
are implemented. An EDF image has not been built or boot-tested yet.

## Starting point

This migration starts at `d87b488`, which fixes first-time PetaLinux source
configuration by removing the generated Kconfig directories from Git. Existing
application and hardware work was carried into this isolated worktree by the
task fork; preserve those local changes when making migration commits.

The first target is the Cora Z7-10 (Zynq-7000, Cortex-A9). The host is native
Ubuntu 22.04.5 x86-64 with Vivado/Vitis 2025.1 installed. It has about 8 GiB RAM,
24 GiB swap, and 107 GiB free on the source filesystem at the initial check.
Limit build concurrency and plan disk usage before starting a full image build.

## Initial EDF release candidate

AMD maps EDF 25.05 to Vivado 2025.1. Start by assessing that matching release
instead of introducing a hardware-tool upgrade at the same time.

- Manifest repository: <https://github.com/Xilinx/yocto-manifests>
- Tag: `amd-edf-rel-v25.05`
- Manifest commit: `4c4924fa3ea52c79450d8214b4f3510cc995b646`
- Manifest: `default-edf.xml`, preserved under `software/edf/manifests/`
- EDF layer commit: `8cf141ada1d43bde66e12e3209fcde31fff587d5`
- Yocto layer series: Scarthgap, matching the shared application layer.

The upstream manifest and EDF layer have been fetched and inspected. The
manifest pins the layer revisions, and the release provides the `amd-edf`
distribution and `edf-init-build-env` entry point.

The pinned `meta-xilinx-standalone-sdt/README.sdt.bsp.md` explicitly describes
Zynq-7000 custom machines. The Cora flow is XSA -> Vitis 2025.1 `sdtgen` ->
`gen-machineconf parse-sdt` -> a Cora machine, with the generated Linux domain
and a minimal Cortex-A9 baremetal multiconfig for FSBL. This uses native
Yocto/EDF tools; PetaLinux's SDK and generated project are not sourced or copied.

The canned 25.05 EDF disk-image package sets list newer 64-bit SoCs. Instead,
`meta-sonar` supplies Cora-specific minimal and application images on the
`amd-edf` distro foundation. It selects SysV init for the existing services,
the Zynq SD boot script, the board's USB/PHY/XADC/DMA policy, and kernel support
for the reusable PWM/DSP modules. EDF bootbin version-header metadata for newer
SoCs is excluded from the Cora bootbin configuration.

## Native Linux setup

Run from a fresh Bash shell without a PetaLinux or cross-SDK environment:

```bash
python3 scripts/setup-edf.py
```

This fetches each immutable revision in the preserved upstream manifest,
including the pinned gen-machine-conf submodule. It additionally fetches the
same pinned SDR layer used by PetaLinux and applies the same explicit
Scarthgap/headless compatibility edits. It does not reset modified source trees
to switch revisions. Sources and all generated artifacts live under the ignored
`software/edf/workspace/` directory.

Install the standard [Yocto Ubuntu prerequisites](https://docs.yoctoproject.org/5.0.8/ref-manual/system-requirements.html#ubuntu-and-debian).
On this Ubuntu 22.04 host, missing utilities can alternatively be downloaded and
unpacked locally, without sudo or changes to system packages:

```bash
bash scripts/setup-edf-host-tools.sh
export EDF_HOST_TOOLS="$PWD/software/edf/workspace/host-tools"
source scripts/activate-edf.sh
```

If all host prerequisites are installed system-wide, omit `EDF_HOST_TOOLS`.
The local utility wrappers scope their libraries to the individual executable;
they do not set a global `LD_LIBRARY_PATH`.

`activate-edf.sh` changes into the build directory using Poky's native
`oe-init-build-env`. Its generated `bblayers.conf` includes the upstream EDF
layers, SDR layer, the unchanged shared application layer, and `meta-sonar`.
Maintained defaults are in `software/edf/conf/local.conf`; independently edited
local configuration is never silently overwritten. Build defaults are two
BitBake tasks, two parser processes, and `make -j2`, with free-space checks.
`EDF_WORKSPACE` can select a different workspace when fetching, activating,
and importing (use the corresponding `--workspace` argument to Python helpers).

## Hardware import and image targets

First build/export the matching Vivado 2025.1 Cora XSA with bitstream using the
existing hardware scripts. After activating EDF, set the paths explicitly:

```bash
# Run these after activate-edf.sh has changed into workspace/build.
SONAR_REPO=/absolute/path/to/zynq-sonar-platform
SONAR_XSA=/absolute/path/to/cora-z7-10-pwm.xsa
python3 "$SONAR_REPO/scripts/import-edf-hardware.py" "$SONAR_XSA"
bitbake -p
bitbake sonar-edf-minimal
# After validating the minimal boot and driver interfaces:
bitbake sonar-edf-image
```

The importer uses `/tools/Xilinx/2025.1/Vitis/bin/sdtgen` by default; override
with `--sdtgen` for another matching 2025.1 installation. `--sdt-only` generates
the initial system device tree without building native tools. It records the
XSA SHA-256 so an old hardware output cannot be silently reused for a new XSA.
The complete machine import may build `esw-conf-native` and its dependencies
for Lopper and embedded-software configuration.

Before hardware import the build selects `zynq-generic` solely for native-tool
bootstrap. Cora images deliberately reject that bootstrap machine. Import then
selects `cora-z7-10-edf`, which requires the generated SDT machine plus maintained
Cora settings. Generated Kconfig trees, DTS/domain outputs, machine files,
bitstream, downloaded sources, and caches stay out of Git.

The intended SD output is a WIC with a FAT boot partition and ext4 rootfs,
containing FSBL/bitstream/U-Boot `BOOT.BIN`, `boot.scr`, `uImage`, and the Linux
DTB. Boot partition contents, FSBL multiconfig, SD boot arguments, device-tree
compilation, and driver/service behavior must pass build and board checks
before these outputs are treated as deployable.

## Validation recorded on 2026-10-29

- All upstream manifest revisions and the gen-machine-conf submodule were
  fetched at their pinned commits. Re-running source setup reused them.
- The local Ubuntu host utilities run both directly and through the symlinks
  BitBake uses for `HOSTTOOLS`.
- Vitis 2025.1 `sdtgen` generated the Cora system device tree, bitstream, and
  PS7 initialization sources from the existing PWM/acoustic-router XSA.
- The generated SDT plus the maintained board include compiled with DTC 1.6.1;
  its node references and DMA phandles resolved.
- Native BitBake parsed 5,780 recipes: 8,290 targets, 1,643 skipped, 26 masked,
  zero errors. The bootstrap-machine and unrelated ZynqMP QEMU ROM warnings
  are expected before Cora machine import.
- The first native build target is `esw-conf-native -c populate_sysroot`.
  Logs are retained in `software/edf/workspace/logs/` (ignored).
- Configuration generation passed repeat-run and unmanaged-file preservation
  checks. Python compilation, Bash syntax, and Git whitespace checks passed.

These checks do not establish a completed cross-toolchain/image build or a
board boot. The XSA input is local; rebuild it with Vivado before reproducing
this from a clean clone. The existing PetaLinux flow remains available.

## Remaining integration work

1. Complete the native configuration-tools build and run the Cora machine
   import. Verify the Linux domain tree, minimal Cortex-A9 FSBL multiconfig,
   and bootbin dependency graph with actual generated metadata.
2. Parse the Cora-specific image targets, inspect the SD boot/rootfs settings,
   and build the cross-toolchain, kernel, device tree, FSBL, and minimal image.
3. Build the sonar application image. Audit runtime service startup, Python
   modules, and shared driver packaging; add the remaining acoustic/GNU Radio
   package set after the minimal system works.
4. Verify clean-checkout setup and incremental builds. Record successful build
   evidence and artifact hashes before treating the images as deployable.
5. In a later board-validation step, check Ethernet, USB/XADC, FPGA/DMA
   interfaces, and sonar/service behavior. No board has been flashed or
   restarted as part of this initial migration work.

## References

- [AMD EDF release alignment](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/embedded-software/embedded-development-framework.html)
- [EDF 25.05 release notes](https://edf.docs.amd.com/en/v25.05/downloads-and-release-notes.html)
- [Pinned EDF build instructions](https://github.com/Xilinx/meta-amd-edf/blob/8cf141ada1d43bde66e12e3209fcde31fff587d5/README.build.md)
