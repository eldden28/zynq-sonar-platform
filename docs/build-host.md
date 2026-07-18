# Host Development Environment

## Current baseline

- Ubuntu 22.04.5 LTS on x86_64
- Vivado 2025.1 build 6140274 installed and command-line launch verified
- PetaLinux 2025.1 ARM platform installed and environment verified
- GNU Radio 3.10.1.1 from Ubuntu packages
- Python 3.10.12
- GCC/G++ 11.4.0
- CMake 3.22.1
- Ninja 1.10.1
- pkg-config 0.29.2
- Persistent 24 GiB swap file
- AMD/Xilinx 2025.1 vendor host dependencies installed

Exact compatibility decisions remain in `docs/version-matrix.md`.

## Inspect the host

Run the non-destructive host report from the repository root:

```bash
scripts/check-host.sh
```

The script does not install packages or change host configuration. To retain a
local snapshot without committing it:

```bash
mkdir -p reports
scripts/check-host.sh > reports/host-$(date +%F).txt
```

## Activate AMD/Xilinx tools

The project does not modify global shell startup files. After installation,
activate the selected tools explicitly:

```bash
source scripts/activate-tools.sh
```

The default installation root is `/tools/Xilinx/2025.1`. The script uses
`settings64.sh` when present and otherwise supports the 2025.1 unified layout
by adding the Vivado and Vitis launcher directories to `PATH`. A different
installation can be selected without editing the script:

```bash
FPGA_TOOLS_ROOT=/opt/Xilinx FPGA_TOOLS_VERSION=2025.1 \
  source scripts/activate-tools.sh
```

Validate the environment with:

```bash
vivado -version
command -v vivado
```

Validate that the Cora Z7-10 device is installed:

```bash
vivado -nolog -nojournal -mode batch \
  -source hardware/vivado/tcl/check_target_part.tcl \
  -tclargs xc7z010clg400-1
```

Expected output includes `TARGET_PART_OK=xc7z010clg400-1` and `FAMILY=zynq`.

Vitis and PetaLinux are not assumed to be installed merely because Vivado is
available. When present, the activation script sources PetaLinux's separate
environment from `/tools/Xilinx/2025.1/PetaLinux/tool/settings.sh` and verifies
that `PETALINUX_VER` is `2025.1`.

PetaLinux 2025.1's `petalinux-util` command does not implement the historical
`--webtalk` or `--version` options. Verify the active release with:

```bash
printf '%s\n' "$PETALINUX_VER"
```

## Launch GNU Radio Companion

```bash
gnuradio-companion
```

The installed GNU Radio command and Python module have both been verified.
