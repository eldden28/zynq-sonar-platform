# Combined Cora demonstration

The SD image includes the control dashboard, forward sonar, triplet bearing-time display, DVL and LBL/iUSBL navigation, OFDM image and acoustic text modems, conventional BPSK/QPSK/FSK/MFSK, JANUS, PWM, and the FPGA DSP/AXIS acoustic path. The main dashboard starts at `http://192.168.10.2:8080/`. Its **Demonstration features** switches start and stop the seven web services; each card opens its own page. The main dashboard starts at boot, while the feature services start from its switches.

| Feature | Dashboard |
| --- | --- |
| OFDM image modem | `http://192.168.10.2:8081/` |
| OFDM acoustic text modem | `http://192.168.10.2:8082/` |
| DVL navigation | `http://192.168.10.2:8083/` |
| Conventional acoustic modem | `http://192.168.10.2:8084/` |
| Forward sonar | `http://192.168.10.2:8085/` |
| JANUS modem | `http://192.168.10.2:8086/` |
| LBL/iUSBL navigation | `http://192.168.10.2:8087/` |
| Triplet beamforming | `http://192.168.10.2:8088/` |

Navigation's DVL and acoustic pages share one switch. The triplet display starts with a **synthetic** 21-channel scene; real 21-channel input requires the external ADC and DMA device. The sonar and navigation laboratories also use simulated scenes. The acoustic modems can be checked in offline loopback without an audio device; speaker-to-microphone SNR requires a connected playback and capture device.

For a live walkthrough, show the triplet display first and switch it off before starting navigation. Then show the DVL and LBL/iUSBL views, forward sonar, and the modem dashboards one at a time. This leaves CPU capacity for navigation's 100 Hz INS loop.

For the amplified speaker test, connect the speaker to the playback output and a microphone to the capture input, then confirm both devices with `aplay -l` and `arecord -l`. Run the OFDM acoustic text demo's loopback first, then its air mode with the output level low. Record its reported SNR, decoded text, and retries. Use the same microphone position and output setting when comparing it with the headphone speaker.

## Board check on October 5, 2026

The combined SD image booted on the Cora. All seven main-dashboard switches started their services and served the linked pages. Six switches stopped their services cleanly; triplet exposed the defect below. The image exposed `/dev/cora-pwm0`, `/dev/cora-dsp0`, and USB audio playback and capture devices. PWM's GNU Radio smoke test, the byte-exact AXIS loopback, and the FPGA spectral-filter benchmark passed with zero DMA failures. Forward sonar found three simulated targets. Acoustic OFDM text, conventional BPSK/QPSK/FSK/4-FSK, and JANUS offline loopbacks passed, as did a 768-byte OFDM image transfer with matching source and output SHA-256 values. The triplet synthetic CLI demonstration also completed.

The triplet switch exposed one defect: its stop script removed the PID file but left the Python service running. While that process consumed roughly a third of a CPU, navigation's timing check fell below 100 Hz. After a reboot cleared the process, isolated LBL and OFDM iUSBL checks both passed at 100 Hz with valid positions and zero drops. The stop script has been corrected in source; reflash the corrected image before the investor demo, then verify that switching triplet off removes its process. An air-path SNR comparison remains for the amplified-speaker test.

The current image is `software/petalinux/cora-z7-10-baseline/images/linux/petalinux-sdimage.wic` (SHA-256 `3fa88dbd6301cf5de041f90b4a67a74af1c89fa33e19be757a08b1f946af4f6c`). Its root filesystem was inspected to confirm the BusyBox-compatible triplet stop command and the DVL graph fix, and `images/linux/SHA256SUMS` passed. The image awaits a board reflash and triplet on/off check.

## DVL graph repair on October 6, 2026

The DVL page's vehicle-track canvas used a fixed N/E window near zero while the mission positions were hundreds of meters away, so the track was drawn offscreen even as the navigation API supplied fresh samples. The page now fits the viewport to the incoming track, draws north upward, and shows live N/E coordinates. The updated HTML was deployed directly to the running board, its served SHA-256 matched the source, and the live page visibly advanced from N −134.4 m, E −397.0 m to N −132.3 m, E −392.4 m during verification. The navigation service did not need a restart because it serves this asset on each request.

After changing image contents, build the PetaLinux project, package `BOOT.BIN` with the current `system.bit`, then generate the `.wic` with `software/petalinux/cora-z7-10-baseline/package-wic.sh`. The current hardware XSA contains the AXIS acoustic router and spectral filter. Confirm `rootfs.manifest` lists the packages above and that `system.bit` matches the bitstream in the XSA before writing the SD card.
