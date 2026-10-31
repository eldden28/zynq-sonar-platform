# Cora long-form demo

This assembly preserves the original live recordings, AI narration, and captions.

## 00:00:00.000 — Series introduction

# Cora integrated demo series

AI narration: Kokoro af_heart.

## 00:00:00 — Cora demo series agenda

Welcome to the Cora acoustic and navigation demo series. This opening is an agenda for the clips that follow: beamformed forward sonar, O F D M and conventional modems, JANUS interoperability, a simulated navigation mission, the Vivado block design, and measured modem performance.

## 00:00:20 — One Cora board, simultaneous services

This live check shows six board-hosted services responding together on the same Cora Z7-10: sonar, O F D M, conventional modulation, JANUS, and the two navigation views. The Vivado clip explains their common A X I stream hardware path. The later combined video will retain each chapter's evidence and context.

## 00:00:44.100 — Main dashboard

# Cora live dashboard walkthrough

AI narration: Kokoro af_heart.

## 00:00:00 — Live forward-looking sonar

We begin on the live Cora sonar dashboard. Each image is formed from simulated multichannel time-series data, followed by matched filtering and delay-and-sum beamforming. I can zoom the fan, switch to range-versus-angle coordinates, and return to the normal view without stopping the processor.

## 00:00:20 — Array and frequency controls

The controls stage a new array configuration before processing. Here I choose sixty-four elements, a six-hundred-kilohertz center frequency, and one-point-two-five millimeter pitch. Applying the settings runs a new time-series simulation, and the updated channel count appears with the new sonar result.

## 00:00:40 — Edit and process a scene

The scene editor changes the geometry that drives the next simulated receive buffers. I select the first target, rename it, move it to seven-point-two-five meters and twelve degrees, then apply the scene. The dashboard runs another ping and confirms that the custom scene was processed.

## 00:01:00 — OFDM text through programmable logic

Next is the independent acoustic O F D M text terminal. I clear the old receiver, enter a short message, and select the F P G A A X I stream loopback. Transmit sends the generated samples through the Cora D M A path, and the validated receiver reconstructs the same text.

## 00:01:20 — Conventional modulation

The conventional modem remains separate from O F D M. Its menu offers B P S K, Q P S K, two-tone F S K, and multi-tone F S K. For this live example I use Q P S K at five hundred symbols per second and pass the waveform through the same hardware loopback.

## 00:01:41 — JANUS interoperability packet

The JANUS page exercises the open first-contact waveform using the reference implementation. I select the hardware loopback, keep parameter set one, and transmit an application-type-three packet. The decoded class, type, application data, and green C R C result provide visible end-to-end evidence.

## 00:02:02 — Choose the path for the test

These pages now share a clear test progression. Use the software channel for modem development, the two-hundred-fifty-kilosample hardware loopback for D M A and programmable-logic bring-up, and USB audio when physical transducers are attached. The visible JANUS result closes this live board-backed tour.

## 00:03:08.750 — Operations and concurrent dashboards

# Cora operations and concurrent dashboards

AI narration: Kokoro af_heart.

## 00:00:00 — Authenticated hardware dashboard

The main dashboard on port 8080 uses HTTP Basic authentication. Once signed in, it is the board-health page: CPU load, memory, Ethernet, FPGA state, and XADC monitor readings are visible in one authenticated session.

## 00:00:18 — Built-in web terminal

The same protected page includes a browser terminal running as the unprivileged petalinux user. Here it runs hostname and uptime, which confirms the session is attached to the live Cora board without exposing any credentials.

## 00:00:34 — Progressive OFDM image page

Port 8081 is the separate progressive OFDM image-transfer dashboard. It is independently addressable, with its own transfer controls, payload progress, retry status, and reconstructed image view, while the main dashboard remains available.

## 00:00:52 — Concurrent dashboards

These two live board pages can be open together. On the left, 8080 continues monitoring CPU and XADC health. On the right, the OFDM text terminal on 8082 remains ready for modem work. The services are independent but deployed on the same Cora image.

## 00:04:21.250 — Vivado block design

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

## 00:06:46.950 — Modem summary and benchmarks

# Cora modem performance summary

AI narration: Kokoro af_heart.

## 00:00:00 — Tested USB audio results

First, these are preserved live USB speaker-to-microphone tests, not simulations. The fastest clean physical-path run delivered 8,131 payload bits per second using 8 P S K, rate-one-half coding, F F T 256, and a 1.5-to-15-kilohertz band.

## 00:00:21 — Bandwidth and reliability tradeoffs

The comparison also records failures. Wider bands and weaker coding were not consistently better: Q P S K rate two-thirds failed in one wide profile, while 8 P S K rate two-thirds became marginal. The reliable 8 P S K result retained rate-one-half forward error correction.

## 00:00:42 — Sustained FPGA loopback

For the raw hardware path, I sent 1,048,576 deterministic samples in 2,048 frames. Every sample returned byte-exact. The P L is paced at 250,000 samples per second; sustained host throughput measured 148,498 after fixed 512-sample Linux and D M A transaction overhead.

## 00:01:08 — Live Cora end-to-end result

Here is the live Cora dashboard retaining the longest peak-setting run: 16,384 bytes, 43 of 43 packets, zero retries, zero packet errors, and a matching source and receive S H A 256. Useful payload throughput was 3.01 kilobits per second.

## 00:01:30 — 250 ksps at 30 kHz

The water numbers are an engineering projection, not a benchmark. Assuming a transducer chain that truly passes 20 to 40 kilohertz, F F T 1024 and a 256-sample prefix, expected validated payload spans about 7 to 18 kilobits per second across the three profiles.

## 00:01:52 — Practical first-water target

I would plan the first field trial around 5 to 12 kilobits per second using Q P S K rate one-half and adaptive carriers. At 30 kilohertz, absorption is about 8.3 decibels per kilometer, and one meter per second of motion creates roughly 20 hertz of Doppler shift.

## 00:08:59.650 — Navigation and mission dashboard

# Cora simulated navigation mission walkthrough

AI narration: Kokoro af_heart.

## 00:00:00 — Live simulated river mission

This is the live Cora navigation simulator running a synthetic mission on the West Branch Susquehanna River. The dashboard combines acoustic observations, fused inertial navigation, clock status, mission waypoints, and the vehicle attitude model while the simulated vehicle moves along the mapped route.

## 00:00:20 — Two-dimensional mission map

The two-dimensional street map shows the full mission in geographic coordinates. Numbered diamonds mark the upstream and return waypoints, purple markers identify the transponders, and separate overlays track simulation truth, fused I N S, bottom-lock samples, acoustic fixes, and horizontal uncertainty.

## 00:00:41 — Mission and positioning controls

The mission controls let us change the positioning architecture and simulated environment without restarting the service. Here I select four-hydrophone inverted U S B L, retain the dedicated coded acoustic waveform, and apply the configuration. The live dashboard confirms the update.

## 00:01:01 — Pause and resume the mission

Mission execution can be paused for inspection and resumed from the same state. The controls report each transition, while the simulator preserves the route, fused solution, and sensor settings. I then return to the map and fit the complete river mission again.

## 00:01:19 — Satellite view of the river

Switching to MapTiler satellite imagery reveals the actual river corridor, islands, bridges, roads, and shoreline beneath the same mission overlays. The browser loads the imagery directly without storing the key on Cora. Follow mode then centers the moving vehicle while preserving its planned route and navigation tracks.
