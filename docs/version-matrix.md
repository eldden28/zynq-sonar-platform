# Tool and Platform Version Matrix

This file is the source of truth for host, vendor-tool, target, and build-tool
compatibility. A selected version is a project decision; a detected version is
what is visible in the current environment. Do not infer one from the other.

Last host/target inspection: 2026-08-15

| Component | Selected Version | Detected Version | Status | Notes |
|---|---:|---:|---|---|
| Ubuntu | 22.04.5 LTS | 22.04.5 LTS | Validated | Supported by Vivado and PetaLinux 2025.1; both platform hardware builds completed on this host. |
| Vivado | 2025.1 | 2025.1 build 6140274 | Detected | Installed at `/tools/Xilinx/2025.1/Vivado`; command-line launch verified. |
| Vitis | 2025.1 | 2025.1 build 6137779 | Detected | Installed at `/tools/Xilinx/2025.1/Vitis`; optional for the current scripted Vivado/PetaLinux builds. |
| PetaLinux | 2025.1 | 2025.1 ARM | Detected | Installed at `/tools/Xilinx/2025.1/PetaLinux/tool`; environment and host prerequisite checks pass with a nonfatal missing-TFTP warning. |
| Target kernel | PetaLinux 2025.1 default | 6.12.10-xilinx | Validated | Boots from the SD card's ext4 partition on the 32-bit ARM Zynq-7000 target. |
| GNU Radio host | 3.10.1.1 | 3.10.1.1 | Detected | Ubuntu package `3.10.1.1-2`; command and Python module verified. |
| GNU Radio target | 3.10.12 | 3.10.12.0 (`cd20ee25fb`) | Validated | Headless ARMv7 runtime, blocks, analog, digital, FFT, filter, network, Python bindings, and VOLK; on-board flowgraph passed. |
| Python host | Ubuntu 3.10 | 3.10.12 | Detected | Ubuntu system `python3`. |
| Python target | PetaLinux 2025.1 default | 3.12.9 | Validated | Python bindings and the GNU Radio smoke test run on the target. |
| CMake | Ubuntu 22.04 | 3.22.1 | Detected | Ubuntu package `3.22.1-1ubuntu1.22.04.2`. |
| GCC host | Ubuntu 22.04 | 11.4.0 | Detected | Ubuntu system compiler. |
| Cross compiler | PetaLinux 2025.1 default | GCC 13.3.0 | Validated | Produces ARMv7-A hard-float/NEON packages for the Cortex-A9 target. |
| Board BSP | Repository baseline | Digilent board files 1.1 | Validated | Cora Z7-10 physical Rev. B uses the Digilent `B.0` Vivado definition and the repository's template-based PetaLinux project rather than a vendor BSP. |

Additional detected host build tools:

| Component | Detected Version | Status | Notes |
|---|---:|---|---|
| Ninja | 1.10.1 | Detected | Ubuntu package `ninja-build 1.10.1-1`. |
| pkg-config | 0.29.2 | Detected | Ubuntu package `pkg-config 0.29.2-1ubuntu3`. |

## Recorded host resources

- Architecture: x86_64
- Kernel at initial inspection: 6.8.0-40-generic
- Logical memory: approximately 7.6 GiB RAM
- Swap at initial inspection: none
- Current swap: persistent 24 GiB `/swapfile`, verified 2026-07-16
- Repository filesystem free space: approximately 373 GiB

These values are observations, not compatibility approvals. Re-run
`scripts/check-host.sh` to produce a current report.

## Vendor dependency installation

The Vivado 2025.1 `installLibs.sh` script was run on 2026-07-17. Required
Ubuntu compatibility and development packages were installed, including
`libtinfo5`, `libncurses5`, `libncurses5-dev`, 32-bit C development support,
Xvfb, GTK development libraries, and Vitis-related host dependencies.

The initial installer post-processing stalled because `libtinfo.so.5` was
missing. After installing the compatibility packages, Vivado and Vitis both
launched successfully. The Vivado installed-device list was regenerated and
the target `xc7z010clg400-1` was verified as family `zynq`.

## Remaining compatibility inputs

- Vivado 2025.1 edition, installer filename, checksum, and license model
- Long-term host GNU Radio alignment with the 3.10.12 target
- Exact Vitis requirements for future standalone accelerator software
- Long-term target package set once accelerator transport requirements are known
