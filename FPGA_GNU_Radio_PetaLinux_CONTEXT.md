# FPGA-Accelerated GNU Radio on PetaLinux

## 1. Project Overview

This project will create a reproducible development environment for building GNU Radio applications that use FPGA-based DSP acceleration on an AMD/Xilinx platform.

The development host is an Ubuntu machine running Codex. The host will be configured with:

- AMD/Xilinx Vivado
- AMD/Xilinx Vitis
- AMD/Xilinx PetaLinux tools
- Supporting build dependencies
- GNU Radio development tools where useful

The target system will run PetaLinux and include GNU Radio. Custom FPGA IP blocks will perform computationally intensive DSP operations. Software interfaces will expose those accelerators as normal GNU Radio blocks.

The desired end state is a repeatable workflow in which a new hardware accelerator can be:

1. Added to the FPGA design.
2. Described in the device tree.
3. Exposed through a Linux driver or userspace interface.
4. Wrapped by a reusable host library.
5. Presented as a GNU Radio out-of-tree block.
6. Tested and benchmarked against a software implementation.

A second critical objective is to preserve a PC-centered development workflow. Developers must be able to build and test GNU Radio flowgraphs on the development PC, optionally exercise FPGA hardware remotely through the connected PetaLinux system, and then publish validated flowgraphs to the embedded target.

---

## 2. Primary Objectives

### 2.1 Development Environment

Create a documented and reproducible Ubuntu development environment containing compatible versions of:

- Vivado
- Vitis
- PetaLinux
- Required Ubuntu packages
- Cross-compilation tools
- Board support files
- GNU Radio development dependencies
- Source-control and automation tools

The setup process should be scripted wherever practical. Manual installation steps that cannot be automated must be clearly documented.

### 2.2 Base PetaLinux System

Create a PetaLinux project that:

- Boots reliably on the selected target board.
- Includes network access and SSH.
- Supports persistent deployment and debugging.
- Includes the runtime libraries required by GNU Radio.
- Provides access to FPGA peripherals and DMA resources.
- Can be rebuilt from source without relying on undocumented local state.

### 2.3 GNU Radio Integration

Build GNU Radio for the PetaLinux target and validate that:

- The GNU Radio runtime starts successfully.
- A basic flowgraph can execute on the target.
- Custom out-of-tree modules can be cross-compiled.
- Flowgraphs can be deployed without rebuilding the entire image.
- Logging, profiling, and debugging are available.
- Python support is included only if its footprint and maintenance cost are acceptable.
- Host-developed flowgraphs can be published to the embedded target with minimal changes.

### 2.4 FPGA Hardware Acceleration

Develop a reusable architecture for exposing FPGA DSP accelerators to GNU Radio.

The architecture should support:

- Streaming sample data.
- Block-oriented or frame-oriented processing.
- Configuration and status registers.
- AXI4-Stream datapaths.
- AXI4-Lite control interfaces.
- DMA-based transfers.
- Interrupt-driven completion where useful.
- Cache-coherent buffer handling where supported.
- Multiple hardware accelerator instances.
- Runtime discovery of accelerator capabilities.
- Measurement of latency, throughput, CPU utilization, and copy overhead.

### 2.5 Host-Based Development and Remote Hardware Testing

Preserve a workflow in which:

- GNU Radio Companion and host-side GNU Radio tools run on the development PC.
- Flowgraphs can be tested entirely in software on the PC.
- Selected blocks can execute on remote FPGA hardware through the connected PetaLinux target.
- Software and hardware results can be compared automatically.
- A validated flowgraph can be published to and run locally on the embedded system.
- Moving between software, remote-hardware, and local-hardware modes requires minimal flowgraph modification.

---

## 3. Current Background

A separate project already runs GNU Radio in a Buildroot-based Linux system. That project can be used as a reference for:

- GNU Radio package selection.
- Cross-compilation requirements.
- Runtime dependencies.
- Existing flowgraphs.
- Existing FPGA interfaces.
- Existing GNU Radio out-of-tree modules.
- Host-connected flowgraph development.
- Remote hardware testing.
- Lessons learned from embedded deployment.

A previous hardware communication method used GNU Radio IIO integration, or `gr-iio`. That method worked but was cumbersome to configure and maintain.

This project should evaluate whether `gr-iio` remains appropriate or whether a simpler custom interface would provide a better long-term workflow.

---

## 4. Architectural Direction

The preferred architecture separates GNU Radio-facing code, the accelerator API, execution backends, and low-level device access.

```text
GNU Radio Flowgraph
        |
        v
Custom GNU Radio OOT Block
        |
        v
Common Accelerator API
        |
        +----------------------+-----------------------+
        |                      |                       |
        v                      v                       v
Software Backend        Remote Backend          Local Backend
PC reference code       Network connection      Device driver
                               |                       |
                               v                       v
                        PetaLinux service        FPGA accelerator
                               |
                               v
                        Local accelerator API
                               |
                               v
                        Linux device driver
                               |
                               v
                        FPGA accelerator
```

The GNU Radio block should not directly contain low-level device-management code or network protocol code. Low-level access should live in a reusable accelerator runtime library.

This separation allows the same accelerator to be:

- Tested outside GNU Radio using command-line utilities.
- Executed as a software reference on the host.
- Invoked remotely from a host flowgraph.
- Invoked locally from an embedded flowgraph.
- Benchmarked at each interface layer.

---

## 5. Candidate Linux Interface Strategies

The project should evaluate these approaches before committing to a production design.

### 5.1 Userspace I/O with DMA Support

Use UIO for control registers and a dedicated DMA buffer mechanism for sample transfers.

Potential benefits:

- Relatively simple driver model.
- Fast development.
- Register access is easy to inspect and debug.
- Suitable for early prototypes.

Potential limitations:

- Buffer allocation and cache coherency require careful handling.
- Interrupt and DMA behavior may require additional kernel support.
- Security and process-isolation concerns must be considered.
- A generic UIO-only design may become awkward as the number of accelerators grows.

### 5.2 Custom Platform Driver

Develop a Linux platform driver for each accelerator class or for a common accelerator framework.

The driver would expose:

- Configuration and status operations.
- Buffer allocation or mapping.
- DMA submission.
- Completion events.
- Error reporting.
- Capability information.
- Performance counters where available.

Potential userspace interfaces include:

- Character devices.
- `ioctl` operations.
- `mmap` for shared buffers.
- `poll` or `epoll` for completion events.
- `read` and `write` for simple control operations.

Potential benefits:

- Strong control over the interface.
- Better long-term scalability.
- Clear ownership of buffers and interrupts.
- Easier enforcement of consistent behavior across accelerators.

Potential limitations:

- More kernel development and maintenance.
- Kernel-version compatibility must be managed.
- The ABI must be designed carefully.

### 5.3 Industrial I/O and `gr-iio`

Continue using Linux IIO and GNU Radio’s IIO integration.

Potential benefits:

- Existing Linux subsystem.
- Standard interfaces for supported device models.
- Existing GNU Radio integration.
- Useful for converter-like devices and sample streams.

Potential limitations:

- Configuration may become cumbersome for arbitrary DSP accelerators.
- Not every accelerator maps naturally onto the IIO abstraction.
- Large numbers of heterogeneous hardware blocks may create maintenance overhead.

### 5.4 DMA-BUF-Based Architecture

Use DMA-BUF or a related shared-buffer mechanism to reduce unnecessary copies between components.

Potential benefits:

- Can support zero-copy or reduced-copy pipelines.
- Buffers may be shared between devices and userspace.
- Potentially strong performance for chained accelerators.

Potential limitations:

- Greater implementation complexity.
- Cache coherency and synchronization must be explicit.
- Integration with GNU Radio’s buffer model requires careful design.

### 5.5 Initial Recommendation

Begin with a narrow proof of concept using:

- AXI4-Lite for control.
- AXI DMA or an equivalent supported DMA engine for data.
- A small platform driver or established DMA interface.
- A C++ userspace accelerator runtime.
- A software backend.
- One local hardware backend.
- One GNU Radio out-of-tree block.

Add a remote backend after the local runtime and standalone test application work reliably.

Avoid designing a large generic accelerator framework until the first accelerator has been demonstrated and measured.

The first implementation should be intentionally small but structured so that common components can later be extracted.

---

## 6. Hardware Accelerator Contract

Each FPGA accelerator should follow a documented contract wherever practical.

### 6.1 Control Plane

Use an AXI4-Lite register interface for:

- Core identification.
- Interface version.
- Capability flags.
- Configuration values.
- Start, stop, reset, and abort operations.
- Current operating state.
- Error status.
- Performance counters.
- Input and output sizes.
- Optional scaling or numeric-format information.

Suggested identification fields include:

- Magic value.
- Accelerator type.
- Hardware revision.
- Register-map version.
- Feature flags.

### 6.2 Data Plane

Prefer AXI4-Stream for high-throughput sample movement.

The interface contract must define:

- Sample format.
- Samples per transfer.
- Byte ordering.
- Fixed-point representation where applicable.
- Channel layout.
- Packet or frame boundaries.
- Meaning of `TLAST`.
- Backpressure behavior.
- Minimum alignment.
- Maximum transfer size.
- Behavior for partial transfers.
- Error recovery behavior.

### 6.3 Versioning

Hardware, driver, library, remote protocol, and GNU Radio interfaces must be versioned.

A software component should detect incompatible hardware or protocol versions rather than silently producing incorrect results.

At minimum, track:

- Register-map version.
- Data-format version.
- Driver ABI version.
- Userspace library API version.
- Remote protocol version.
- GNU Radio block version.

---

## 7. GNU Radio Block Design

Each hardware-backed GNU Radio block should behave as much like a normal GNU Radio block as possible.

The block should:

- Validate input and output item sizes.
- Configure the selected backend during initialization.
- Acquire and release resources safely.
- Submit data without unnecessary copies.
- Handle backpressure correctly.
- Report device and transport errors clearly.
- Stop and reset the accelerator cleanly.
- Support deterministic shutdown.
- Expose useful configuration through block parameters.
- Avoid embedding board-specific paths or hostnames in source code.
- Provide a software fallback when practical.
- Preserve consistent observable behavior across software, remote, and local modes.

The GNU Radio-facing code should remain thin. Device discovery, DMA operations, register access, network transport, and error translation should live in the accelerator runtime.

A possible source structure is:

```text
gr-fpga-accelerators/
├── CMakeLists.txt
├── README.md
├── cmake/
├── include/
│   └── fpga_accelerators/
├── lib/
│   ├── accelerator_block_impl.cc
│   └── accelerator_block_impl.h
├── python/
├── grc/
├── apps/
├── examples/
└── tests/
```

The reusable device library may be maintained separately:

```text
fpga-accelerator-runtime/
├── CMakeLists.txt
├── include/
│   └── fpga_runtime/
├── src/
│   ├── software/
│   ├── local/
│   └── remote/
├── protocol/
├── tools/
├── tests/
└── docs/
```

---

## 8. Host-Based Flowgraph Development and Remote Hardware Testing

A critical requirement is preserving a PC-centered GNU Radio development workflow.

Developers must be able to create, modify, visualize, and test GNU Radio flowgraphs on the Ubuntu development PC before publishing them to the embedded PetaLinux target.

The intended workflow is:

1. Build a flowgraph using GNU Radio Companion or host-side GNU Radio tools.
2. Run the flowgraph on the development PC using software implementations of the DSP blocks.
3. Optionally run the same flowgraph on the PC while selected blocks execute on FPGA hardware connected through the PetaLinux target.
4. Compare software and hardware results.
5. Measure throughput, latency, buffering behavior, and resource use.
6. Publish the validated flowgraph and required modules to the embedded target.
7. Run the flowgraph locally on the PetaLinux system without requiring the development PC.

### 8.1 Execution Modes

Hardware-capable GNU Radio blocks should support three execution modes wherever practical:

- **Software mode:** The algorithm runs entirely on the development PC using a reference software implementation.
- **Remote-hardware mode:** GNU Radio runs on the development PC, while accelerator operations are executed by the FPGA through a connected PetaLinux target.
- **Local-hardware mode:** GNU Radio and the accelerator runtime both run on the embedded PetaLinux target.

The flowgraph interface, block parameters, data types, and observable behavior should remain consistent across these modes.

A block should not require redesign merely because execution moves from the PC to the embedded system.

### 8.2 Runtime Backend Selection

The accelerator runtime should provide interchangeable backends behind one common API.

Possible backend selection mechanisms include:

- Block parameter.
- Configuration file.
- Environment variable.
- Device URI.

Example device identifiers might eventually resemble:

```text
software://
local:///dev/fpga-accel0
remote://target-hostname/fpga-accel0
```

The exact URI format should be selected only after the first proof of concept.

### 8.3 Remote Hardware Service

Remote-hardware mode will likely require a lightweight service running on the PetaLinux target.

The service should:

- Discover available FPGA accelerators.
- Report accelerator identity, version, and capabilities.
- Accept configuration requests.
- Receive input buffers.
- Submit work through the local runtime and driver.
- Return output buffers and completion status.
- Report hardware and transport errors.
- Support clean cancellation and shutdown.
- Expose performance statistics.
- Reject incompatible protocol or hardware versions.

The remote service should reuse the same local accelerator runtime used by embedded GNU Radio. It should not create a second independent FPGA-control implementation.

The initial transport may use a straightforward TCP-based protocol. More specialized transports should only be considered after measuring the initial implementation.

### 8.4 Remote Development Requirements

The host-connected workflow must support:

- GNU Radio Companion on the PC.
- Full host-side logging and visualization.
- Software-only execution without target hardware.
- Remote access to one or more FPGA accelerators.
- Clear selection between software, remote, and local backends.
- Detection of disconnected or incompatible targets.
- Automatic capability and version checks.
- Bounded buffering and backpressure.
- Configurable request and completion timeouts.
- Recovery from interrupted network connections.
- Comparison of software and hardware output.
- Collection of transport and accelerator performance statistics.

### 8.5 Flowgraph Portability

A validated PC flowgraph should be publishable to the embedded target with minimal changes.

Ideally, deployment should require changing only:

- The selected execution backend.
- Device identifiers.
- Target-specific file paths.
- Parameters related to the embedded system’s available resources.

Algorithm topology, block interfaces, sample formats, and functional parameters should remain unchanged.

Where host and target capabilities differ, those differences must be detected explicitly and reported clearly.

### 8.6 Software Reference Implementations

Each FPGA-accelerated block should retain a host-executable software implementation.

The software implementation serves as:

- A functional reference.
- A development fallback when hardware is unavailable.
- A source of expected test results.
- A way to validate complete flowgraphs before hardware deployment.
- A comparison point for performance measurements.
- A mechanism for isolating whether failures originate in the flowgraph, transport, driver, or FPGA logic.

Software and hardware implementations should use the same documented numeric behavior, including:

- Sample representation.
- Scaling.
- Rounding.
- Saturation.
- Overflow.
- Framing.
- State initialization.
- Reset behavior.

### 8.7 Host-to-Target Publishing

The repository should eventually provide a deployment command or script that can:

- Cross-compile required GNU Radio modules.
- Package flowgraphs and configuration files.
- Package shared libraries and runtime dependencies.
- Verify target compatibility.
- Transfer artifacts to the PetaLinux system.
- Install them into a versioned deployment directory.
- Run validation checks.
- Start the embedded flowgraph.
- Collect logs and execution results.
- Roll back to the previous deployment when installation fails.

Generated GNU Radio Companion flowgraphs should be stored in source control alongside any generated runtime code required for deployment.

### 8.8 Testing Requirements

Every hardware-backed GNU Radio block should be tested in all applicable modes:

1. Host software mode.
2. Host GNU Radio with remote FPGA execution.
3. Embedded GNU Radio with local FPGA execution.

Tests should confirm that all modes produce equivalent results within the defined numeric tolerance.

End-to-end tests should also compare:

- Flowgraph startup behavior.
- Output correctness.
- Buffer-boundary behavior.
- Backpressure behavior.
- Repeated start and stop.
- Error propagation.
- Timeout handling.
- Target disconnection.
- Throughput.
- Latency.
- CPU utilization.
- Network overhead.
- DMA overhead.

---

## 9. Proposed Repository Layout

A top-level workspace may use the following organization:

```text
fpga-gnu-radio-platform/
├── CONTEXT.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── version-matrix.md
│   ├── build-host.md
│   ├── petalinux.md
│   ├── gnuradio.md
│   ├── driver-model.md
│   ├── remote-runtime.md
│   ├── deployment.md
│   └── troubleshooting.md
├── scripts/
│   ├── check-host.sh
│   ├── setup-environment.sh
│   ├── build-petalinux.sh
│   ├── build-gnuradio.sh
│   ├── build-oot-module.sh
│   ├── deploy-target.sh
│   └── run-remote-tests.sh
├── hardware/
│   ├── vivado/
│   ├── ip/
│   ├── constraints/
│   └── export/
├── petalinux/
│   ├── project-spec/
│   ├── meta-user/
│   └── recipes/
├── kernel/
│   ├── drivers/
│   ├── device-tree/
│   └── uapi/
├── runtime/
│   ├── include/
│   ├── src/
│   ├── service/
│   ├── protocol/
│   ├── tools/
│   └── tests/
├── gnuradio/
│   ├── oot/
│   ├── flowgraphs/
│   └── tests/
├── deploy/
└── third_party/
```

Generated build products, vendor installation files, and large binary images should not be committed unless explicitly required.

---

## 10. Implementation Phases

### Phase 0: Record Target Information

Before installing or building anything, record:

- Ubuntu version.
- CPU architecture.
- Available RAM.
- Available disk space.
- Target FPGA or SoC.
- Target development board.
- Board revision.
- Boot method.
- Serial-console settings.
- JTAG availability.
- Network configuration.
- Existing BSP availability.
- Required vendor-tool versions.
- License requirements.
- Secure Boot or host-security constraints.

Create `docs/version-matrix.md` and treat it as the source of truth for compatibility.

### Phase 1: Host Validation

Create a host-check script that reports:

- Distribution and kernel version.
- Disk capacity.
- Memory.
- Required shells and utilities.
- Required development libraries.
- Locale configuration.
- Tool installation paths.
- License visibility.
- Environment variables.
- Whether supported versions are being used.

Do not immediately modify the host. First generate a report of missing or incompatible dependencies.

### Phase 2: Vendor Tool Installation

Install Vivado, Vitis, and PetaLinux according to the selected version matrix.

Document:

- Installer names and checksums.
- Installation paths.
- Required permissions.
- License setup.
- Environment setup scripts.
- Host-package workarounds.
- Validation commands.
- Known warnings.

The installation should not depend on undocumented changes to global shell startup files. Prefer an explicit project environment script.

Example:

```bash
source scripts/activate-tools.sh
```

The activation script should validate that all expected tools are present and that their versions match the project configuration.

### Phase 3: Minimal Hardware Platform

Create or import the simplest bootable hardware platform for the selected board.

At this stage:

- Do not add custom accelerator IP.
- Validate clocks, resets, memory, UART, Ethernet, and storage.
- Export the hardware description needed by PetaLinux.
- Record the exact source and generation process for the exported platform.

### Phase 4: Base PetaLinux Image

Create a minimal PetaLinux image with:

- Serial console.
- SSH server.
- Network utilities.
- Package-management strategy, if appropriate.
- Debugging tools needed during development.
- Kernel module support.
- Required DMA and FPGA-management support.
- Device-tree overlays only if there is a clear need.
- A service-management strategy for the remote accelerator service.

Validate:

- Clean boot.
- Stable networking.
- File deployment over SSH.
- Kernel logs.
- FPGA device enumeration.
- Reproducible rebuild.

### Phase 5: GNU Radio Cross-Compilation

Determine the smallest practical GNU Radio feature set.

Evaluate whether the target requires:

- C++ runtime only.
- Python bindings.
- GNU Radio Companion-generated Python flowgraphs.
- VOLK.
- FFT libraries.
- Boost components.
- Logging frameworks.
- ZeroMQ.
- Audio support.
- GUI components.

GUI components should be excluded from the embedded image unless there is a specific requirement.

Prefer running headless flowgraphs on the target.

Build GNU Radio as either:

- Native PetaLinux/Yocto recipes.
- An external cross-compiled package integrated into the root filesystem.
- A dedicated application layer consumed by the PetaLinux build.

The chosen method must allow incremental development without rebuilding unrelated components.

### Phase 6: First Accelerator Proof of Concept

Select a simple, easily verified DSP accelerator. Good candidates include:

- Vector gain.
- Complex conjugation.
- Complex multiplication.
- FIR filtering.
- Magnitude-squared.
- Sample-format conversion.

The first accelerator should be simple enough that correctness is obvious and a software reference implementation is readily available.

Implement:

- Hardware IP.
- AXI4-Lite control registers.
- AXI4-Stream input and output.
- DMA path.
- Device-tree entry.
- Linux interface.
- Standalone userspace test.
- Common accelerator API.
- Software backend.
- Local hardware backend.
- GNU Radio block.
- Automated comparison against software output.

### Phase 7: Remote Hardware Backend

After the local accelerator path works:

- Define a versioned remote protocol.
- Implement the PetaLinux accelerator service.
- Implement a host-side remote backend.
- Add capability discovery.
- Add timeouts, cancellation, and reconnect behavior.
- Validate host GNU Radio operation using the remote FPGA.
- Compare remote hardware output with software and local hardware output.
- Measure network transport overhead.

### Phase 8: Host-to-Target Publishing

Create a repeatable workflow to:

- Build host and target variants.
- Cross-compile OOT modules.
- Package flowgraphs.
- Verify target compatibility.
- Deploy artifacts.
- Execute post-deployment checks.
- Launch the embedded flowgraph.
- Collect logs.
- Roll back failed deployments.

### Phase 9: Performance Characterization

Measure:

- Sustained throughput.
- End-to-end latency.
- CPU utilization.
- Memory bandwidth.
- Number of memory copies.
- DMA setup overhead.
- Network transport overhead.
- Performance at different buffer sizes.
- Accelerator utilization.
- GNU Radio scheduler behavior.
- Error and recovery behavior.

Do not describe a path as zero-copy without verifying each buffer transition.

### Phase 10: Reusable Accelerator Framework

After the first proof of concept works, identify repeated elements and extract them into reusable components.

Potential common components include:

- Device discovery.
- Register-map access.
- DMA buffer management.
- Buffer queues.
- Completion handling.
- Capability negotiation.
- Error reporting.
- Statistics collection.
- Backend selection.
- Remote protocol support.
- GNU Radio block helpers.
- Test-vector infrastructure.
- Device-tree templates.
- Driver skeletons.
- Deployment tooling.

Only generalize behavior demonstrated by at least one working accelerator.

---

## 11. Testing Strategy

Testing must occur at multiple layers.

### 11.1 Hardware Simulation

Where practical, validate:

- Register behavior.
- AXI4-Stream handshaking.
- Packet boundaries.
- Reset behavior.
- Backpressure.
- Numeric correctness.
- Overflow and saturation behavior.

### 11.2 Standalone Runtime Tests

Before integrating GNU Radio, create a command-line test program that can:

- Discover the device.
- Read its identity and version.
- Configure it.
- Allocate or map buffers.
- Send known input vectors.
- Receive output.
- Compare output against expected data.
- Exercise repeated transfers.
- Test timeout and reset behavior.
- Display performance statistics.
- Select software, local, or remote backends.

### 11.3 GNU Radio Tests

GNU Radio tests should include:

- Block creation.
- Parameter validation.
- Known-vector tests.
- Repeated start and stop.
- Flowgraph shutdown.
- Invalid-device handling.
- Incorrect hardware-version handling.
- Incorrect protocol-version handling.
- Backpressure behavior.
- Software-versus-hardware comparison.
- Remote disconnection and recovery.
- Host-to-target portability.

### 11.4 Hardware-in-the-Loop Tests

Provide scripts that deploy artifacts to the target, execute tests, and collect:

- Test results.
- Kernel logs.
- Service logs.
- Application logs.
- Hardware version.
- Image version.
- Protocol version.
- Git commit identifiers.
- Performance measurements.

### 11.5 Cross-Mode Equivalence Tests

For each accelerated block, compare:

- Host software mode.
- Host remote-hardware mode.
- Embedded local-hardware mode.

Tests must define numeric tolerances and explicitly account for fixed-point, rounding, scaling, saturation, and framing behavior.

---

## 12. Reproducibility Requirements

A clean checkout should provide enough information to recreate:

- The host environment.
- The Vivado project.
- Custom FPGA IP.
- The exported hardware platform.
- The PetaLinux project.
- Kernel modules.
- Device-tree modifications.
- GNU Radio.
- Custom GNU Radio modules.
- Accelerator runtime backends.
- The remote accelerator service.
- Deployment artifacts.
- Test applications.

Scripts must fail clearly when prerequisites are missing.

Do not silently download or modify dependencies without recording:

- Source URL or package source.
- Version.
- Checksum.
- License.
- Installation location.

Record Git commit identifiers in generated images and deployment packages where practical.

---

## 13. Configuration Principles

Avoid hard-coded values for:

- Device paths.
- Physical addresses.
- Interrupt numbers.
- DMA channel names.
- Clock rates.
- Board revisions.
- Installation directories.
- Network addresses.
- Service ports.
- Cross-compiler prefixes.

Prefer configuration through:

- Device tree.
- Generated platform metadata.
- CMake configuration.
- Environment files.
- Command-line arguments.
- Device URIs.
- Explicit project configuration files.

Board-specific code should be isolated from generic accelerator code.

---

## 14. Logging and Error Handling

All components must return actionable errors.

Errors should identify:

- The failed operation.
- The affected device or remote endpoint.
- Relevant operating-system or transport error information.
- Expected and detected versions.
- Possible recovery actions where known.

The runtime library should expose structured errors rather than requiring GNU Radio blocks to parse log strings.

Debug logging should be configurable at runtime.

Kernel code must avoid excessive logging in high-rate data paths.

The remote service and client should use correlation identifiers or equivalent request tracking so failures can be traced across the network boundary.

---

## 15. Security and Reliability Considerations

Even for a development system:

- Validate all userspace parameters passed to kernel code.
- Validate all remote message lengths, types, and identifiers.
- Validate buffer lengths and alignment.
- Prevent userspace from accessing arbitrary physical memory.
- Handle process termination while DMA is active.
- Handle network disconnection while work is active.
- Reset hardware after failed or timed-out operations.
- Define ownership rules for each accelerator.
- Prevent simultaneous incompatible configurations.
- Avoid exposing unrestricted register maps unless intentionally required.
- Document whether the system assumes a trusted userspace and trusted network environment.
- Avoid transmitting native C++ structures directly over the network.
- Version and validate the remote protocol.

---

## 16. Decisions to Avoid Prematurely

Do not commit too early to:

- A universal driver for every possible accelerator.
- A single buffer size.
- Python-only GNU Radio blocks.
- A zero-copy claim.
- Device-tree overlays.
- Dynamic partial reconfiguration.
- Chaining multiple hardware accelerators without returning to memory.
- A custom GNU Radio scheduler.
- A complex RPC framework.
- A production ABI before the first proof of concept.
- Specialized network transports before measuring TCP performance.
- Host and target GNU Radio ABI compatibility without verification.

These may become useful later, but they should be justified by measurements and concrete requirements.

---

## 17. Open Questions

The following information still needs to be filled in:

- Exact Ubuntu release.
- Exact AMD/Xilinx device.
- Exact development board and revision.
- Installed RAM and available disk space.
- Vivado, Vitis, and PetaLinux version requirements.
- Whether a vendor BSP will be used.
- Whether the target is Zynq-7000, Zynq UltraScale+ MPSoC, Versal, or another platform.
- Boot source: SD, eMMC, QSPI, network, or another method.
- Whether the ARM processing system is 32-bit or 64-bit.
- Whether GNU Radio Python bindings are required on the target.
- Whether flowgraphs will be generated on the host or authored directly on the target.
- Host GNU Radio version.
- Target GNU Radio version.
- Whether host and target will use the same OOT module source with separate builds.
- Required sample rates.
- Sample formats.
- Number of simultaneous channels.
- Acceptable latency.
- Typical and maximum transfer sizes.
- Acceptable network overhead for remote testing.
- Whether accelerators must be chained directly in programmable logic.
- Whether partial reconfiguration is a future requirement.
- Existing Buildroot source location.
- Existing `gr-iio` or custom block source location.
- Existing remote-control or transport implementation.
- Existing hardware IP and register maps.
- Preferred licensing for new code.

These unknowns should be tracked, but they should not prevent initial repository setup and host inspection.

---

## 18. Instructions for Codex

When working in this repository, Codex should follow these rules:

1. Read this file and the current version matrix before making changes.
2. Inspect existing files before proposing replacements.
3. Never assume a Vivado, Vitis, PetaLinux, Ubuntu, kernel, or GNU Radio version.
4. Detect installed versions where possible.
5. Keep vendor-version-specific logic isolated.
6. Prefer small, reviewable changes.
7. Explain any command that modifies the host.
8. Do not run destructive commands without making the impact clear.
9. Do not remove an existing working configuration merely because another layout is cleaner.
10. Generate scripts that are safe to run repeatedly.
11. Use strict shell behavior where appropriate.
12. Check command return codes.
13. Add useful error messages.
14. Keep generated files out of source control unless the repository explicitly tracks them.
15. Update documentation when build behavior changes.
16. Create tests for reusable runtime components.
17. Establish a standalone userspace test before creating a GNU Radio wrapper.
18. Separate low-level device access from GNU Radio block code.
19. Separate network transport logic from GNU Radio block code.
20. Use one common accelerator API for software, remote, and local backends.
21. Preserve a host software reference implementation for every accelerator.
22. Preserve host-based flowgraph development as a required capability.
23. Do not design blocks that can run only on the embedded target unless technically unavoidable.
24. Preserve a software reference implementation for correctness testing.
25. Measure performance before optimizing.
26. Clearly label assumptions.
27. Do not assume host and target GNU Radio versions are ABI-compatible.
28. Define explicit serialization for remote messages rather than transmitting native C++ structures.
29. Version the remote protocol.
30. Validate all remote message lengths, types, and identifiers.
31. Measure remote transport overhead before attempting specialized zero-copy or network-acceleration designs.
32. Make target deployment reproducible and scriptable.
33. Never claim that a flowgraph is portable until it has been tested in both host and embedded execution modes.
34. Stop and report when an operation requires an installer, license, credential, board connection, or user interaction that is not available.
35. Never fabricate a successful build, boot, deployment, remote test, or hardware test.
36. Record exact commands and relevant logs when diagnosing failures.
37. Prefer source-controlled configuration over undocumented manual GUI state.

---

## 19. Initial Codex Tasks

Codex should begin with the following tasks.

### Task 1: Inspect the Ubuntu Host

Create a non-destructive script named:

```text
scripts/check-host.sh
```

The script should report:

- Ubuntu release.
- Kernel version.
- CPU architecture.
- CPU count.
- Installed memory.
- Free disk space.
- Shell.
- Locale.
- Required package availability.
- Existing AMD/Xilinx tool installations.
- Existing environment variables.
- Existing GNU Radio installation.
- Docker or container availability, if relevant.
- USB and JTAG visibility, if relevant.

The script must not install or remove anything.

### Task 2: Create the Version Matrix

Create:

```text
docs/version-matrix.md
```

Include fields for:

| Component | Selected Version | Detected Version | Status | Notes |
|---|---:|---:|---|---|
| Ubuntu | TBD | TBD | Unknown | |
| Vivado | TBD | TBD | Unknown | |
| Vitis | TBD | TBD | Unknown | |
| PetaLinux | TBD | TBD | Unknown | |
| Target kernel | TBD | TBD | Unknown | |
| GNU Radio host | TBD | TBD | Unknown | |
| GNU Radio target | TBD | TBD | Unknown | |
| Python | TBD | TBD | Unknown | |
| CMake | TBD | TBD | Unknown | |
| GCC host | TBD | TBD | Unknown | |
| Cross compiler | TBD | TBD | Unknown | |
| Board BSP | TBD | TBD | Unknown | |

Do not recommend or install versions until the target board and compatibility requirements are known.

### Task 3: Inventory Existing Work

Create:

```text
docs/existing-work-inventory.md
```

Document any available Buildroot project, GNU Radio modules, FPGA IP, drivers, device trees, remote services, test vectors, and build scripts.

Classify each item as:

- Reusable without changes.
- Reusable with adaptation.
- Reference only.
- Obsolete or unknown.

### Task 4: Draft the Proof-of-Concept Plan

Create:

```text
docs/first-accelerator-poc.md
```

Describe a minimal accelerator implementation using one simple DSP function. Include:

- Data format.
- Register map.
- DMA path.
- Driver or userspace interface.
- Standalone validation program.
- Common accelerator API.
- Software backend.
- Local backend.
- Remote backend plan.
- GNU Radio block.
- Test vectors.
- Performance measurements.
- Success criteria.

Do not implement the accelerator until the target platform information has been recorded.

### Task 5: Document the Host-to-Target Workflow

Create:

```text
docs/host-target-workflow.md
```

Document:

- Host software development.
- Host flowgraph execution.
- Remote FPGA execution.
- Cross-mode comparison.
- Target packaging.
- Target deployment.
- Embedded execution.
- Log and result collection.
- Rollback behavior.

---

## 20. Initial Success Criteria

The first major milestone is complete when:

- The Ubuntu host configuration is documented.
- Compatible vendor-tool versions have been selected.
- Vivado, Vitis, and PetaLinux launch successfully.
- A reproducible PetaLinux project builds.
- The image boots on the target.
- SSH access works.
- A flowgraph can be developed and executed entirely on the PC.
- A headless GNU Radio flowgraph runs on the target.
- A standalone program communicates with one FPGA accelerator.
- A GNU Radio block uses the same common accelerator API.
- The same host flowgraph can use the FPGA accelerator remotely.
- Software and remote-hardware results can be compared automatically.
- The validated flowgraph can be published to the embedded target.
- The embedded flowgraph can run locally without the development PC.
- Hardware output matches a software reference.
- Throughput and latency are measured in software, remote, and local modes.
- The entire procedure is documented from a clean checkout.

---

## 21. Long-Term Direction

After the first accelerator is working, the project may evolve toward:

- A library of reusable FPGA DSP accelerators.
- Standardized register and streaming interfaces.
- Automated generation of driver and block scaffolding.
- Shared DMA-buffer infrastructure.
- Direct hardware-to-hardware streaming.
- Runtime accelerator discovery.
- Hardware/software fallback blocks.
- Remote accelerator discovery and scheduling.
- Automated hardware-in-the-loop testing.
- Continuous integration for software-only components.
- Reproducible PetaLinux image releases.
- Versioned host-to-target deployment bundles.
- Performance regression testing.
- Partial reconfiguration where justified.

The long-term goal is not merely to run GNU Radio on PetaLinux. It is to create an efficient and repeatable development workflow for moving suitable DSP workloads between GNU Radio software and FPGA hardware while preserving rapid PC-based flowgraph development and testing.
