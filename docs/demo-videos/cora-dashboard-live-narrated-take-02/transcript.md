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
