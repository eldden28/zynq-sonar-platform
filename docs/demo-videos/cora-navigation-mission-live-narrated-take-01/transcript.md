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

Switching to satellite imagery reveals the actual river corridor, islands, bridges, roads, and shoreline beneath the same mission overlays. This view uses public U S G S National Map imagery with visible attribution. Follow mode then centers the moving vehicle while preserving its planned route and navigation tracks.
