# EDF migration on Linux

Status: the Cora Z7-10 sonar WIC has been built with native EDF 25.05 on Linux.
The initial image passed offline checks and SD-card readback verification, but
the first board boot stopped after `Starting kernel ...`. Investigation found
a missing Zynq platform identifier in the Linux device tree. The board policy
now supplies that identifier, and the image verifier requires it. Hardware
boot and runtime validation of the corrected image remain pending.

## Starting point

This migration starts at `d87b488`, which fixes first-time PetaLinux source
configuration by removing the generated Kconfig directories from Git. Existing
application and hardware work was carried into this isolated worktree by the
task fork; preserve those local changes when making migration commits.

The first target is the Cora Z7-10 (Zynq-7000, Cortex-A9). The host is native
Ubuntu 22.04.5 x86-64 with Vivado/Vitis 2025.1 installed. It has about 8 GiB RAM,
24 GiB swap, and 107 GiB free on the source filesystem at the initial check.
Limit build concurrency and plan disk usage before starting a full image build.

## Pinned EDF release

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

`meta-sonar` backports the upstream pseudo `openat2` syscall fix and old-header
compatibility definitions. Updated Ubuntu tar 1.34 packages use that syscall;
the original EDF 25.05 pseudo cannot track its directory descriptors, causing
`Cannot mkdir: Bad address` during packaging. The patched native tool passed
an isolated nested-tar extraction test on this host. The patch is scoped to
native/SDK pseudo and does not change the host's installed tar package.

`activate-edf.sh` changes into the build directory using Poky's native
`oe-init-build-env`. Its generated `bblayers.conf` includes the upstream EDF
layers, SDR layer, the reused shared application layer, and `meta-sonar`.
Maintained defaults are in `software/edf/conf/local.conf`; independently edited
local configuration is never silently overwritten. Build defaults are two
BitBake tasks, three parser processes, and `make -j2`, with free-space checks.
The Cora-specific sstate class uses 16 threads for HTTP cache lookup with a
copy of the datastore; compilation remains limited to two tasks. Both Linux
and baremetal multiconfigs use an explicit EDF release pin for mirror URLs.
The image profile masks unused ROS recipes and excludes the vendor QEMU
system-emulator dependencies. `rm_work` removes completed intermediate trees
while preserving the native configuration tools needed for hardware import.
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
# Smaller optional bring-up image:
# bitbake sonar-edf-minimal
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

The SD output is a WIC with a 512 MiB FAT32 boot partition and ext4 rootfs,
containing FSBL/bitstream/U-Boot `BOOT.BIN`, `boot.scr`, `uImage`, and the Linux
DTB. The image explicitly installs `os-release` so SysV images expose the same
OS identity interface as other EDF profiles. The Cora bootbin append strips
whitespace left after removing the unsupported EDF optional version header.

After building, verify the actual image without mounting it:

```bash
EDF_IMAGES="$SONAR_REPO/software/edf/workspace/build/tmp/deploy/images/cora-z7-10-edf"
python3 "$SONAR_REPO/scripts/verify-edf-wic.py" \
  "$EDF_IMAGES/sonar-edf-image-cora-z7-10-edf.rootfs.wic" \
  --manifest "$EDF_IMAGES/sonar-edf-image-cora-z7-10-edf.rootfs.manifest" \
  --report "$EDF_IMAGES/sonar-edf-image-cora-z7-10-edf.validation.json"
```

The verifier needs `mcopy` and `debugfs`; the local host-tools setup includes
mtools. EDF keeps the default `amd-edf` account: first login uses the serial
console and requires setting a password before password-based SSH access.


## Initial image and evidence (2026-10-29, superseded)

Outputs are under `software/edf/workspace/build/tmp/deploy/images/cora-z7-10-edf/`.
The initial image is `sonar-edf-image-cora-z7-10-edf.rootfs-20261029223652.wic`.
It is 794,968,064 bytes
(about 758 MiB). A `.wic.gz` and `.wic.bmap` are also provided.

Raw WIC SHA-256:

```text
3b3cb82149f0fb957a3ed5aa6e3c75f35c6557c07404fda11ccaaec6ff627a0a
```

`SHA256SUMS`, `sonar-edf-image-cora-z7-10-edf.validation.json`, the transport
validation report, package manifest, and provenance record accompany it.
Build logs are in `software/edf/workspace/logs/`; the final run is
`sonar-image-build-09.log`. Generated artifacts stay out of Git.

- The final run completed all 8,494 tasks successfully, with 8,462 reused.
  Its 14 warnings concern masked ROS layers and unrelated ZynqMP QEMU ROM
  support. No build errors remain.
- The image contains the dashboard, sonar service, triplet beamformer,
  navigation service, acoustic modem, NumPy, and PWM/DSP kernel modules.
  Verification checked their package entries and actual files in ext4,
  including module autoload configuration.
- The MBR has an active FAT32 partition at sector 8 (1,048,576 sectors) and
  an ext4 partition at sector 1,048,584 (504,088 sectors). Boundaries,
  non-overlap, and the ext4 superblock passed inspection.
- `BOOT.BIN`, `boot.scr`, `uImage`, and `system.dtb` were extracted from FAT
  and checked. The kernel and script headers and CRCs pass; the script selects
  SD partition 2 and `ttyPS0,115200`. The DTB contains the maintained Cora
  PWM/DSP/USB definitions. Boot-file hashes match deployed artifacts.
- Bootgen readback confirms four bootbin partitions: FSBL, FPGA bitstream,
  U-Boot, and the Linux domain DTB. Readback and bootbin input logs are retained
  in `workspace/logs/checks/`.
- The compressed image expands to the verified raw WIC, and all mapped ranges
  in the bmap match their checksums.
- The built kernel enables the SD controller, ext4, devtmpfs, Ethernet/PHY,
  AXI DMA, USB host/generic PHY, and XADC. Correcting two USB fragment symbol
  names produced a byte-identical final `.config`; the final kernel and
  modules were rebuilt from the maintained fragment.
- Native configuration tools, SDT generation, Linux domain import, Cortex-A9
  FSBL multiconfig, and both cross-toolchains completed. Configuration helpers
  passed repeat-run, timestamp stability, and unmanaged-file preservation
  checks; Python/Bash syntax and Git whitespace checks passed.

The XSA input hash is
`76a951528cb8874b77f7e60acd11507cba4d0be9171bbe50f9aa34d0dcaffd19`.
It came from the existing Vivado 2025.1 PWM/acoustic-router export. The build
also uses inherited local application changes in the shared layer; source
hashes and a tracked-source patch are retained with provenance. This is not
proof that every input is already committed on GitHub. A clean-clone hardware
rebuild must include those changes and regenerate the XSA.

## First boot investigation (2026-10-30)

The initial WIC was written to the user's 7.5 GiB removable microSD and its
entire image length read back with direct I/O. The SHA-256 matched the source.
U-Boot then loaded the script, kernel, and DTB successfully and verified the
kernel CRC, but no Linux messages appeared after `Starting kernel ...`.

The deployed Linux DTB's root `compatible` contained only `xlnx,cora-z7-10`.
The built kernel's Zynq machine descriptor matches `xlnx,zynq-7000`, so that
DTB did not select the Zynq platform's initialization callbacks. Maintained
`cora-board.dtsi` now provides both identifiers. `KERNEL_COMMAND_APPEND` also
adds `earlycon` while keeping the existing 115200-baud console. The verifier
parses the root property and requires the SoC identifier, and checks that the
boot script enables early console output. Offline tests exercised the original
and corrected DTBs, plus truncated blobs. Board testing is still needed to
establish how far the corrected image boots.

The `Loading Environment from FAT` / `bad env area` warning means U-Boot is
using its compiled defaults because it found no valid saved environment. The
subsequent successful script and kernel loads show this did not prevent the
boot attempt. `FDT_ERR_NOTFOUND` comes from the script reading an absent
`/chosen/bootargs`; it subsequently supplies console and root arguments.

To test the compatibility correction on the initial card without saving any
environment or rewriting the SD card, stop autoboot and run:

```text
fatload mmc 0:1 0x00300000 uImage
fatload mmc 0:1 0x00200000 system.dtb
fdt addr 0x00200000
fdt resize 0x1000
fdt set / compatible "xlnx,cora-z7-10" "xlnx,zynq-7000"
setenv bootargs console=ttyPS0,115200 earlycon root=/dev/mmcblk0p2 ro rootwait
bootm 0x00300000 - 0x00200000
```

## Remaining board validation

Boot this image in a separately authorized board-validation step and check
serial startup, Ethernet, USB audio, XADC channels, FPGA DMA interfaces, module
binding, and sonar/dashboard/navigation services. Offline verification does
not establish hardware behavior. The existing PetaLinux workflow is preserved.

## References

- [AMD EDF release alignment](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/embedded-software/embedded-development-framework.html)
- [EDF 25.05 release notes](https://edf.docs.amd.com/en/v25.05/downloads-and-release-notes.html)
- [Pinned EDF build instructions](https://github.com/Xilinx/meta-amd-edf/blob/8cf141ada1d43bde66e12e3209fcde31fff587d5/README.build.md)
