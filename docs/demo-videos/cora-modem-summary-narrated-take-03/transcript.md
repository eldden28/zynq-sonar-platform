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
