# Unified multi-platform architecture

## Goal

Build one sonar and communications solution from common application, DSP,
dashboard, and driver sources while allowing each physical hardware set to
carry its own Vivado, boot, device-tree, and deployment policy.

The unit of platform identity is the complete hardware set, not just the SoC.
An UltraZed 3EG on the Avnet PCIe carrier and the same SOM on a custom carrier
are different platforms because their pins, clocks, resets, peripherals,
power sequencing, and validation evidence differ.

## Ownership boundaries

| Layer | Canonical location | Shared across platforms? |
|---|---|---:|
| Python/C/C++ applications and dashboards | `software/cora-*` | Yes |
| Reusable RTL and software-visible ABIs | `hardware/rtl`, `software/cora-{dsp,pwm}` | Yes |
| Yocto application recipes | `software/petalinux/meta-platform-common` | Yes |
| Platform registry and routing | `platforms/<platform>/platform.json` | No |
| Vivado PS preset, carrier pins and block-design wrapper | `platforms/<platform>/hardware` | No |
| Device tree, kernel/U-Boot fragments, boot policy | platform PetaLinux overlay | No |
| Generated XSA, bitstream, DTB, rootfs and boot image | paths named by `platform.json` | Rebuilt per platform |
| Release hashes | `platform-manifest.json` in a release bundle | Rebuilt per release |

Applications must not branch on board names. They should consume a stable
userspace API and capability data. Drivers may bind to different device-tree
nodes, DMA address widths, or interconnects while preserving the common API.
If hardware cannot implement an ABI faithfully, introduce a new ABI version
rather than a board-specific application fork.

## Registry model

`schemas/platform.schema.json` defines the source-time platform record. It
captures:

- stable textual and numeric platform identity;
- SoC architecture and PetaLinux template;
- module/carrier combination;
- hardware handoff and platform overlay paths;
- PetaLinux project and shared layers;
- available common components and required capabilities;
- deployment identity;
- files and read-only commands used for snapshots.

Use:

```sh
python3 scripts/platformctl.py list
python3 scripts/platformctl.py validate
python3 scripts/platformctl.py show ultrazed-3eg-pcie
```

`schemas/platform-manifest.schema.json` serves a different purpose: it records
the hashes and ABI identity of generated release artifacts. Do not combine the
source registry with the release manifest.

## Shared Linux source

PetaLinux builds the same recipe sources separately for ARMv7-A Cora and
AArch64 UltraZed. This produces architecture-specific packages without
forking application revisions. The shared layer currently owns all common
application recipes plus the reusable DMA client modules. Platform `meta-user`
layers own only hardware description and machine policy.

The Cora project loads:

```text
third_party/meta-sdr
software/petalinux/meta-platform-common
project-spec/meta-user                  # Cora-only policy
```

An UltraZed project loads the same two shared layers and a different
`meta-user` overlay. Recipe changes therefore affect both on their next build.

## UltraZed 3EG rollout

Bring up the Avnet PCIe-carrier platform in increasing-risk stages:

1. Start from a vendor reference design or verified board preset for the exact
   UltraZed 3EG SOM and PCIe carrier revision.
2. Export a PS-only or minimal-PL XSA and boot a `zynqMP` PetaLinux image.
3. Validate UART, DDR4, eMMC/SD boot, Ethernet, time, reboot, and sustained
   memory/network load.
4. Install and benchmark the CPU-only common sonar package.
5. Add the common identity peripheral and validate platform ID `0x00020001`.
6. Add AXI DMA loopback. Use ZynqMP high addressing and a 40-bit DMA address
   width, then test transfers above and below 4 GiB where practical.
7. Add FFT/filter and acquisition blocks one at a time, retaining the common
   stream/register ABI.
8. Publish a release bundle only after XSA, bitstream, DTB, rootfs and manifest
   hashes agree.

Do not use the old Avnet PetaLinux BSP as the application source of truth. It
may supply board knowledge and reference configuration, but the project should
be regenerated with the repository's supported 2025.1 toolchain and imported
XSA.

## Snapshot and rollback

Create a configuration snapshot before changing a validated platform:

```sh
python3 scripts/platformctl.py snapshot cora-z7-10 \
  --label before-change --live --ssh-key ~/.ssh/cora_codex_ed25519
```

This writes a JSON record and deterministic source-input archive beneath the
platform's `snapshots/` directory. It records the Git state, every selected
input's SHA-256, and optional read-only target diagnostics. Credentials and
files named `token`, `credentials`, or `auth.json` are excluded.

Restore into an empty review directory first, compare the recorded hashes,
and selectively apply the required inputs. Never unpack a snapshot directly
over a dirty working tree.
