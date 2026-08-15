# Cora Z7-10 hardware overlay

The validated Cora hardware generators remain in `hardware/vivado/tcl` while
the legacy tree is transitioned. `platform.json` is the single routing record
for the active generator and exported XSA.

Board-specific inputs are:

- Digilent Cora Z7-10 Rev. B board preset;
- `hardware/vivado/tcl/create_cora_z7_10_pwm.tcl`;
- Cora pin constraints under `hardware/constraints`;
- device-tree policy in the Cora PetaLinux project's `meta-user` layer.

Reusable RTL stays under `hardware/rtl`; do not copy it into this directory.

The most recent clean implementation record is
`build-2026-08-15.md`. Generated Vivado projects, reports, bitstreams, and XSA
files remain ignored build products; the Tcl generator and pinned board-file
fetch script are the reproducible sources.
