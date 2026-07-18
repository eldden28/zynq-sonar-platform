# Tool and Platform Version Matrix

This file is the source of truth for host, vendor-tool, target, and build-tool
compatibility. A selected version is a project decision; a detected version is
what is visible in the current environment. Do not infer one from the other.

Last host inspection: 2026-07-16

| Component | Selected Version | Detected Version | Status | Notes |
|---|---:|---:|---|---|
| Ubuntu | TBD | 22.04.5 LTS | Unknown | Vendor support must be checked against every selected tool. |
| Vivado | 2025.1 | 2025.1 build 6140274 | Detected | Installed at `/tools/Xilinx/2025.1/Vivado`; command-line launch verified. |
| Vitis | TBD | 2025.1 build 6137779 | Detected | Installed at `/tools/Xilinx/2025.1/Vitis`; launcher version verified. Selection remains open until PetaLinux/BSP compatibility is decided. |
| PetaLinux | 2025.1 | 2025.1 ARM | Detected | Installed at `/tools/Xilinx/2025.1/PetaLinux/tool`; environment and host prerequisite checks pass with a nonfatal missing-TFTP warning. |
| Target kernel | TBD | Not applicable | Unknown | Initial target is a 32-bit ARM Zynq-7000; select with PetaLinux. |
| GNU Radio host | TBD | 3.10.1.1 | Detected | Ubuntu package `3.10.1.1-2`; command and Python module verified. |
| GNU Radio target | TBD | Not applicable | Unknown | Feature set and build method remain undecided. |
| Python | TBD | 3.10.12 | Detected | System `python3`. |
| CMake | TBD | 3.22.1 | Detected | Ubuntu package `3.22.1-1ubuntu1.22.04.2`. |
| GCC host | TBD | 11.4.0 | Detected | Ubuntu system compiler. |
| Cross compiler | TBD | Not applicable | Unknown | Must target ARMv7-A; determined by the selected PetaLinux release. |
| Board BSP | TBD | Digilent board files 1.1 | Partial | Cora Z7-10 physical Rev. B verified; Digilent `B.0` Vivado definition validated. PetaLinux BSP availability remains open. |

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

## Unresolved compatibility inputs

- Available vendor BSP and its supported tool release
- Required Vitis and PetaLinux releases
- Vivado 2025.1 edition, installer filename, checksum, installation path, and license model
- Target boot source and processor architecture
- Host and target GNU Radio feature/version requirements
