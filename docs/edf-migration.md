# EDF migration on Linux

Status: initial compatibility assessment and release pinning. An EDF image has
not been built or boot-tested yet.

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

This release's prebuilt-machine examples cover ZynqMP and Versal rather than
the Cora board. Cora therefore needs an explicit custom-machine assessment;
the matching Vivado version alone does not establish board support.

## Migration work

1. Assess the pinned `meta-xilinx` Zynq-7000 custom-hardware flow, including
   XSA import versus SDT generation, FSBL, device tree, and boot image assembly.
   Confirm whether EDF 25.05 is sufficient before finalizing the release.
2. Set up a separate native Yocto workspace with the required host tools.
   Keep downloaded layers, caches, build directories, and generated machine
   files out of Git. Do not source PetaLinux's environment for EDF builds.
3. Reuse `software/petalinux/meta-platform-common` as a Yocto layer. Audit
   recipes for PetaLinux assumptions, source locations, init services, GNU
   Radio dependencies, and the reusable DSP kernel module.
4. Port the Cora-specific kernel, device-tree, U-Boot, rootfs, and boot-media
   settings into a maintained machine/BSP layer and image configuration.
5. Validate metadata parsing, build a minimal bootable image, then add the
   sonar application set. Check Ethernet, FPGA/DMA interfaces, and service
   behavior before attempting board validation.
6. Validate a clean source checkout and incremental builds. Record actual
   build and boot evidence before marking the EDF platform as validated.

## References

- [AMD EDF release alignment](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/embedded-software/embedded-development-framework.html)
- [EDF 25.05 release notes](https://edf.docs.amd.com/en/v25.05/downloads-and-release-notes.html)
- [Pinned EDF build instructions](https://github.com/Xilinx/meta-amd-edf/blob/8cf141ada1d43bde66e12e3209fcde31fff587d5/README.build.md)
