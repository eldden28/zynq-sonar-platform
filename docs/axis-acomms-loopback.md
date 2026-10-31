# Acoustic AXI-stream loopback and accelerator slot

The Cora design has a runtime-selectable AXI4-Stream path for bringing up
acoustic communication cores without changing the Linux DMA interface.

## Block-design path

```text
DDR
  │
  ▼
axi_dma_dsp / MM2S
  │  S_AXIS_HOST
  ▼
axis_acomms_router_0 ── M_AXIS_ACCELERATOR ──► FFT ─► mask ─► IFFT ──┐
  │                                                                  │
  │  loopback mode                         S_AXIS_ACCELERATOR ◄───────┘
  │
  └── M_AXIS_HOST ──► axi_dma_dsp / S2MM ──► DDR
```

The router is at `0x43c20000`. Its 100 MHz clock and reset divider of 400
produce the requested default rate of **250,000 samples/s**. The divider is
runtime-programmable through the Linux ABI. Pacing is applied at the return
side and propagates ordinary AXI backpressure through an installed
accelerator.

The Linux device remains `/dev/cora-dsp0`:

- `loopback` selects a byte-exact MM2S → AXIS router → S2MM path;
- `accelerator` selects MM2S → installed IP chain → router → S2MM;
- each transaction contains 512 signed 16-bit real samples, carried in the
  low half of a 32-bit AXIS word;
- `TLAST` marks sample 511 and must be preserved by an accelerator;
- the upper 16 bits are the signed imaginary component for the existing FFT
  chain and are zero for real input.

## Verify the shipped hardware

On a built image, run:

```sh
cora-axis-loopback-test --frames 32 --sample-rate 250000
```

The test uses deterministic random data, checks every returned sample,
checks the hardware sample/frame counters, and ensures elapsed time is
consistent with the configured pacing. No other process may hold
`/dev/cora-dsp0` during the test.

For a one-session hot-program test against an older running device tree, the
driver also accepts `router_phys=0x43c20000` as an explicit module parameter.
Normal boot images use the generated `eldden28,cora-dsp-2.0` device-tree node
and never need this override.

The acoustic dashboards expose **FPGA AXIS loopback · 250 ksample/s** in the
channel/backend selector. Dashboard waveforms remain at their native 48
ksample/s modem rate; `cora_acomms_pl` resamples them into the PL rate,
transfers 512-sample frames through hardware, and restores the original rate
before decoding.

## Connect a custom AXI-stream core manually

These steps do not require Codex:

1. Build or validate once with `scripts/build-pwm-hardware.sh validate`. Open
   `build/vivado/cora-z7-10-pwm/cora_z7_10_pwm.xpr` in Vivado 2025.1.
2. Open the `system` block design. Locate `axis_acomms_router_0`,
   `fft_forward`, `axis_spectral_filter_0`, and `fft_inverse`.
3. Disconnect the net from `M_AXIS_ACCELERATOR` to `fft_forward/S_AXIS_DATA`
   and the net from `fft_inverse/M_AXIS_DATA` to
   `S_AXIS_ACCELERATOR`. Remove or retain the old chain as appropriate.
4. Connect `axis_acomms_router_0/M_AXIS_ACCELERATOR` to the input of the new
   processing chain. Connect its output to
   `axis_acomms_router_0/S_AXIS_ACCELERATOR`.
5. Connect every new core to `processing_system7_0/FCLK_CLK0` (100 MHz) and
   `reset_100/peripheral_aresetn`, or add explicit clock converters if the
   core uses another clock domain.
6. Ensure the chain is 32-bit AXIS, honors `TVALID/TREADY` backpressure, and
   preserves `TLAST`. Add AXIS data-width converters explicitly if needed.
7. If the core has AXI4-Lite control, increase `control_interconnect/NUM_MI`,
   connect a new master port, and assign a non-overlapping 64 KiB address
   range. Record the address in the platform device tree or driver.
8. Validate the block design, save it for inspection, then reproduce the
   actual deliverable by applying the same connection changes to
   `hardware/vivado/tcl/create_cora_z7_10_pwm.tcl`. The Tcl file—not the
   generated `.xpr`—is the revision-controlled source of truth.
9. Run `scripts/build-pwm-hardware.sh build`, import the exported XSA with
   `petalinux-config --get-hw-description ... --silentconfig`, and rebuild
   PetaLinux.
10. Run the loopback test first. Then select accelerator mode with the GNU
    Radio `HwFftFilterDevice` or a `CORA_DSP_IOC_SET_PATH` request and test the
    custom algorithm.

Keep the router in the design even when a custom accelerator is installed.
Its loopback setting is a useful known-good diagnostic that separates
DMA/device-tree problems from accelerator logic problems.
