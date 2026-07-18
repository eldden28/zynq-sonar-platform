# Existing Work Inventory

Inventory date: 2026-07-16

The initial workspace inspection found only
`FPGA_GNU_Radio_PetaLinux_CONTEXT.md`. No implementation repositories or
artifacts listed below are currently present in this workspace. This does not
mean that they do not exist elsewhere; their locations have not yet been
provided.

| Area | Located in this workspace | Known external location | Follow-up |
|---|---|---|---|
| Existing Buildroot project | No | Unknown | Record repository path, commit, board configuration, and package selections. |
| GNU Radio OOT modules | No | Unknown | Record source, GNU Radio compatibility, build procedure, and licenses. |
| GNU Radio flowgraphs | No | Unknown | Record `.grc` files, generated files, runtime requirements, and test inputs. |
| FPGA IP and Vivado projects | No | Unknown | Record source, part/board, Vivado version, register maps, and constraints. |
| Hardware exports (`.xsa`) | No | Unknown | Record generating commit and exact tool version; do not treat exports as source. |
| Linux drivers | No | Unknown | Record kernel versions, UAPI, DMA path, and device ownership model. |
| Device-tree sources/overlays | No | Unknown | Record compatible strings, addresses, interrupts, DMA channels, and generation path. |
| Existing `gr-iio` integration | No | Unknown | Capture configuration and known maintenance problems. |
| Remote services/protocols | No | Unknown | Record source, protocol format/version, transport, and failure handling. |
| Test vectors and expected output | No | Unknown | Preserve numeric formats, tolerances, framing, and provenance. |
| Build/deployment scripts | No | Unknown | Record prerequisites, target paths, credentials assumptions, and generated artifacts. |
| Board support packages | No | Digilent board files are available externally | Select and pin a compatible revision after Vivado installation; a PetaLinux BSP has not been selected. |

## Context-derived information

- A separate Buildroot system already runs GNU Radio and may be used as a
  reference.
- That system may contain existing flowgraphs, FPGA interfaces, OOT modules,
  remote hardware testing, and embedded deployment knowledge.
- A previous hardware interface used GNU Radio IIO (`gr-iio`) and worked, but
  was cumbersome to configure and maintain.

These statements come from the project context and have not yet been verified
against source files or a running system.

## Intake checklist

When external work is made available, record it without copying generated or
licensed vendor content into source control unintentionally:

1. Repository or filesystem location and responsible owner.
2. Git remote, branch, commit, and dirty-worktree state where applicable.
3. Tool, kernel, GNU Radio, board, and BSP versions.
4. Build and deployment commands plus required environment variables.
5. License and redistribution restrictions.
6. Known-good tests, test vectors, logs, and measured performance.
7. Generated artifacts and the source-controlled inputs that reproduce them.
