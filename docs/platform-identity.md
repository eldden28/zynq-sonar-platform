# FPGA Platform Identity and Compatibility Contract

## Purpose

Software must determine what hardware is actually programmed. A bitstream
filename, deployment path, or remembered programming operation is not evidence
of the active design.

Every platform design shall contain a read-only identity peripheral. Every
published platform bundle shall contain a manifest. Before accessing DMA or an
accelerator, software shall compare its requirements with both the manifest and
the identity values read from hardware.

## Identity register block version 1

All registers are 32-bit little-endian AXI4-Lite words. The identity block base
address is platform-specific and recorded in the device tree and manifest.
Offsets through `0x3c` are reserved for the common ABI.

| Offset | Name | Description |
|---:|---|---|
| `0x00` | `MAGIC` | `0x46504741`, ASCII `FPGA` |
| `0x04` | `IDENTITY_ABI` | Common identity register ABI; initially `1` |
| `0x08` | `PLATFORM_ID` | Stable numeric board/platform identifier |
| `0x0c` | `PLATFORM_REVISION` | Revision of the platform-level hardware design |
| `0x10` | `REGISTER_ABI` | Common accelerator register ABI |
| `0x14` | `DATA_ABI` | Common stream/framing ABI |
| `0x18` | `BUILD_ID_0` | Least-significant word of the 128-bit build ID |
| `0x1c` | `BUILD_ID_1` | Build ID word 1 |
| `0x20` | `BUILD_ID_2` | Build ID word 2 |
| `0x24` | `BUILD_ID_3` | Most-significant word of the build ID |
| `0x28` | `CAPABILITIES_0` | Platform capability bits 31:0 |
| `0x2c` | `CAPABILITIES_1` | Platform capability bits 63:32 |
| `0x30` | `ACCELERATOR_COUNT` | Number of discoverable accelerator instances |
| `0x34` | `BUILD_UNIX_TIME` | Informational build time; zero for reproducible builds |
| `0x38` | `STATUS` | Identity/platform status; zero means ready |
| `0x3c` | `SCRATCH` | Optional read/write bus test; never used as identity |

The build ID is generated from version-controlled hardware inputs and build
parameters, not from a filename. A truncated source hash may be used initially;
the manifest retains the complete Git commit and artifact SHA-256 values.

## Compatibility policy

Before DMA or accelerator register access, the runtime shall:

1. Read `MAGIC` and stop if it does not match.
2. Require the supported `IDENTITY_ABI`.
3. Match `PLATFORM_ID` to the intended target.
4. Check the required platform, register, and data ABI versions.
5. Compare the hardware `BUILD_ID` to the active manifest.
6. Check required capability bits and accelerator inventory.
7. Report expected and detected values on every mismatch.

The driver should probe only the identity block until the checks needed for
safe access have passed. Userspace must not be able to override a mismatch with
an unversioned boolean flag. A development override, if later required, must be
explicit, logged, and unavailable by default.

## Platform identifiers

| ID | Name | Device | Status |
|---:|---|---|---|
| `0x00010001` | `cora-z7-10` | XC7Z010-1CLG400C | Reserved for this project |

Platform ID identifies the board-level design contract, not an accelerator.
Accelerators have separate type and version identifiers so the same algorithm
can be used on multiple platforms.

## Platform bundle

A deployable release is an indivisible versioned bundle containing:

```text
manifest.json
design.bit
design.xsa
system.dtb
```

The manifest must contain SHA-256 hashes for every deployed artifact. Files may
be absent during development, but a bundle must not be marked deployable until
all required artifacts and hashes are present.

The initial boot workflow installs the bitstream, DTB, and manifest together.
Device-tree overlays are deferred until runtime platform switching is a proven
requirement.

