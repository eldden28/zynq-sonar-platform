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
