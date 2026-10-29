# PetaLinux source checkouts

The Cora projects keep their maintained system configuration in
`project-spec/configs/config`, package selections in
`project-spec/configs/rootfs_config`, and custom recipes and configuration in
`project-spec/meta-user/`.

These generated subdirectories must not be committed:

- `project-spec/configs/configs/`
- `project-spec/configs/rootfsconfigs/`

This applies to both `petalinux/cora-z7-10` and
`software/petalinux/cora-z7-10-baseline`. Committing the generated directories
caused "folder already exists" failures during first-time project setup from
a source checkout. They also contain Kconfig references to the original
workstation's absolute paths. PetaLinux regenerates them during configuration;
the repository ignores them so subsequent builds cannot add them accidentally.

Each project's `.petalinux/.gitignore` keeps the `.petalinux/` marker directory
in a fresh clone. PetaLinux requires that directory to recognize an existing
project. Its generated metadata and other local state remain ignored.

For the existing baseline project in a fresh clone, fetch the SDR layer,
activate PetaLinux 2025.1, build the Cora PWM hardware, and import its XSA:

```bash
scripts/fetch-meta-sdr.sh
source scripts/activate-tools.sh
scripts/build-pwm-hardware.sh build
petalinux-config \
  --project software/petalinux/cora-z7-10-baseline \
  --get-hw-description "$PWD/hardware/export/cora-z7-10-pwm/cora-z7-10-pwm.xsa" \
  --silentconfig
scripts/build-petalinux-baseline.sh
```

The clone already contains this project's maintained configuration, so use
`petalinux-config` to initialize it. `petalinux-create project` and
`scripts/create-petalinux-baseline.sh` require a new project destination.

Updating an existing checkout may remove the formerly tracked generated
files. Run the configuration command again to regenerate them. No manual
deletion of the project or its maintained configuration is needed.
