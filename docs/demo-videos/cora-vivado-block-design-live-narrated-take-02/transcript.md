# Cora Vivado block design walkthrough

AI narration: Kokoro af_heart.

## 00:00:00 — The generated Cora project

This is the actual Vivado twenty-twenty-five-point-one project generated for the Cora Z seven dash ten. The system block design is open in the real Vivado G U I, so the cells, interfaces, clocks, resets, and address-connected peripherals shown here are the implementation source, not a presentation diagram.

## 00:00:22 — Host memory and streaming DMA

The highlighted A X I D M A block is the software-facing entrance and exit for acoustic samples. Its memory-to-stream channel feeds the acoustic router, while stream-to-memory returns each completed frame. Linux therefore keeps one stable device interface even when the processing chain in programmable logic changes.

## 00:00:44 — Known-good AXIS loopback

The acoustic router is the bring-up anchor. In loopback mode it returns the D M A stream directly while preserving valid, ready, and last signaling. That gives new hardware a known-good test before any algorithm core is enabled, and software selects the path without rebuilding the block design.

## 00:01:04 — Installed accelerator chain

The alternate route passes through the forward F F T, the spectral-filter example, and the inverse F F T before returning to the router. This chain demonstrates the insertion contract with a working accelerator while keeping the direct loopback beside it for diagnosis.

## 00:01:22 — Control, clocks, and reset

A X I Lite control reaches the router and spectral filter through the control interconnect. The streaming path uses the one-hundred-megahertz fabric clock and its matching reset network. A custom core should share this domain, or add explicit clock conversion when its interface requires another rate.

## 00:01:42 — Where custom IP connects

To insert your own processing, keep the router and replace the example chain between its accelerator output and accelerator return interfaces. Match the thirty-two-bit A X I stream width, propagate backpressure and T last, then assign any new A X I Lite registers a non-overlapping address before validating the design.

## 00:02:04 — Return to the complete design

Returning to the full canvas shows why this structure is reusable: software and D M A remain fixed, loopback remains available, and only the accelerator segment changes. The checked-in Tcl script regenerates this project, while the exported X S A carries the result into the common PetaLinux build.
