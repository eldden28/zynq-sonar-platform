# Manually add a custom hardware platform

This procedure is intentionally complete enough to follow without Codex. It
assumes AMD Vivado and PetaLinux 2025.1 are installed as described in
`docs/build-host.md`.

## 1. Choose the platform boundary and ID

Treat every SOM/carrier combination as a separate platform. For example, an
UltraZed 3EG moved from the Avnet PCIe carrier to `orca-carrier` becomes a new
directory such as:

```text
platforms/ultrazed-3eg-orca-carrier/
```

Reserve a unique numeric ID in `docs/platform-identity.md`. Never reuse the
PCIe-carrier ID for custom hardware. Increment the platform design revision
when compatible logic changes; allocate a new platform ID when the physical
hardware or incompatible contract changes.

## 2. Copy and edit the definition template

```sh
mkdir -p platforms/ultrazed-3eg-orca-carrier/hardware
mkdir -p platforms/ultrazed-3eg-orca-carrier/petalinux/overlay/project-spec/meta-user/conf
mkdir -p platforms/ultrazed-3eg-orca-carrier/petalinux/overlay/project-spec/meta-user/recipes-bsp/device-tree/files
cp platforms/templates/zynqmp-custom/platform.json.example \
  platforms/ultrazed-3eg-orca-carrier/platform.json
cp platforms/templates/zynqmp-custom/system-user.dtsi.example \
  platforms/ultrazed-3eg-orca-carrier/petalinux/overlay/project-spec/meta-user/recipes-bsp/device-tree/files/system-user.dtsi
cp platforms/templates/zynqmp-custom/device-tree.bbappend.example \
  platforms/ultrazed-3eg-orca-carrier/petalinux/overlay/project-spec/meta-user/recipes-bsp/device-tree/device-tree.bbappend
cp platforms/templates/zynqmp-custom/user-rootfsconfig.example \
  platforms/ultrazed-3eg-orca-carrier/petalinux/overlay/project-spec/meta-user/conf/user-rootfsconfig
```

Replace every placeholder. Confirm:

- directory name and `id` match;
- `platform_id` is unique;
- device is the installed SOM device and speed grade;
- carrier name and revision match the schematic and PCB;
- every path is repository-relative;
- deployment addresses are empty until assigned.

Validate immediately:

```sh
python3 scripts/platformctl.py validate ultrazed-3eg-orca-carrier
```

## 3. Create the Vivado hardware platform

Start from the SOM vendor reference design or verified SOM preset, then apply
the custom carrier schematic. Do not copy the PCIe carrier constraints and
edit until warnings disappear.

Review and document at least:

- DDR4 topology and PS configuration;
- MIO assignments and I/O voltages;
- boot mode, QSPI, eMMC/SD, UART and Ethernet;
- reference clocks, resets and power-good sequencing;
- carrier pin constraints and bank voltages;
- PL clock domains and CDC boundaries;
- PS HP/HPC AXI ports, DMA address width and interrupts;
- PCIe/SATA/USB/DisplayPort resources actually routed on the custom carrier;
- thermal and power limits.

Add shared RTL by referencing `hardware/rtl`; do not make a carrier-local copy.
Preserve the identity/register/data ABI documented in
`docs/platform-identity.md`. Export the XSA to the exact path in
`platform.json`, for example:

```text
hardware/export/ultrazed-3eg-orca-carrier/ultrazed-3eg-orca-carrier.xsa
```

Commit the Tcl/constraints needed to reproduce the design, not the generated
Vivado project.

## 4. Add the PetaLinux overlay

Keep the overlay small. It should contain only custom-carrier device-tree,
kernel, U-Boot, network, and boot policy. Generated hardware nodes come from
the XSA.

The copied `device-tree.bbappend` beside the `files` directory contains:

```bitbake
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = " file://system-user.dtsi"
```

The copied `conf/user-rootfsconfig` selects one desired shared package per
line, initially:

```text
CONFIG_cora-sonar
```

Do not copy common `.bb` recipes into the overlay. They come from
`software/petalinux/meta-platform-common`.

## 5. Bootstrap the PetaLinux project

Activate the tools and let the registry create the project without overwriting
anything:

```sh
source scripts/activate-tools.sh
python3 scripts/platformctl.py bootstrap ultrazed-3eg-orca-carrier
```

The command performs the equivalent manual operations:

1. `petalinux-create project --template zynqMP`;
2. `petalinux-config --get-hw-description <xsa> --silentconfig`;
3. copy the platform overlay into `project-spec/meta-user`;
4. register the common application and meta-sdr layers;
5. regenerate configuration.

Review the generated configuration before building. The bootstrap command
refuses to overwrite an existing project.

## 6. Build and test in stages

```sh
petalinux-build --project software/petalinux/ultrazed-3eg-orca-carrier
```

Validate in this order:

1. serial boot and clean reboot;
2. DDR memory stress;
3. storage and persistent filesystem behavior;
4. Ethernet and SSH;
5. CPU-only `cora-sonar-smoke`;
6. identity register and manifest match;
7. DMA loopback and interrupt/error recovery;
8. FFT/filter pipeline;
9. real acquisition and long-duration thermal testing.

Record failures against the platform overlay or shared component according to
ownership. Fixing a shared algorithm belongs in `software/`; fixing a carrier
clock or PHY belongs only in that platform.

## 7. Snapshot and release

After assigning an address:

```sh
python3 scripts/platformctl.py snapshot ultrazed-3eg-orca-carrier \
  --label first-validated-image --live --ssh-key ~/.ssh/your-platform-key
```

Generate a release `platform-manifest.json` containing hashes for the XSA,
bitstream, DTB and image. Change `status` from `scaffold` to `development`, and
finally to `validated` only when all strict paths and physical tests pass:

```sh
python3 scripts/platformctl.py validate ultrazed-3eg-orca-carrier --strict
```

Never promote a platform solely because Vivado implementation and PetaLinux
compilation succeed; both can pass with incorrect carrier constraints.
