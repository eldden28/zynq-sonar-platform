# Shared PetaLinux application layer

This Yocto layer is architecture-neutral. It packages the canonical sources
under `software/` for every supported PetaLinux platform. Recipes must not
contain board addresses, pin assignments, PHY descriptions, boot media
assumptions, or carrier-card policy.

Platform projects add this as an external user layer. Their own `meta-user`
layer owns device trees, kernel and U-Boot fragments, machine configuration,
and image selection.

Keep application behavior portable through capability discovery. A platform
may omit an FPGA-backed package or expose the same userspace ABI through a
different DMA/interconnect implementation.
