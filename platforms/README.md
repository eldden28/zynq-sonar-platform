# Hardware platform registry

Each deployable hardware set has one directory containing a `platform.json`
definition and only its platform-specific overlays. A hardware set means the
combination of SOM/board, carrier, PL design contract, and boot policy. Changing
the carrier creates a new platform even when the SOM is unchanged.

The registry currently contains:

- `cora-z7-10`: validated Cora Z7-10 Rev. B configuration;
- `ultrazed-3eg-pcie`: UltraZed 3EG plus Avnet PCIe carrier development build;
- `templates/zynqmp-custom`: manual starting files for a custom carrier.

Use `scripts/platformctl.py list`, `show`, `validate`, `snapshot`, and
`bootstrap` to operate on definitions. `platform.json` routes tools to source
inputs; `platform-manifest.json` remains the release-time artifact/hash record.

Shared code does not live under `platforms/`:

- applications: `software/cora-*`;
- reusable RTL: `hardware/rtl`;
- shared Yocto recipes: `software/petalinux/meta-platform-common`;
- ABI and stream contracts: `docs/platform-identity.md` and component docs.
