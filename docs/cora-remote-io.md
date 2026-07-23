# Cora Remote I/O over Ethernet

Cora Remote I/O v1 makes board hardware available to a GNU Radio flowgraph on
the development PC without moving the board-specific IIO, DMA, or PWM driver
into the PC. It uses two TCP byte streams. The ADC stream works with GNU
Radio's stock `network` TCP Source; the PWM stream uses the supplied Cora TCP
Float Sink because it provides dependable reconnect and shutdown behavior.

| Direction | Board TCP endpoint | GNU Radio item type | Purpose |
|---|---:|---|---|
| Board → PC | `192.168.10.2:50000` | normalized `float32` | ADC samples |
| PC → Board | `192.168.10.2:50001` | normalized `float32` | PWM duty samples |

Both are contiguous IEEE-754 little-endian float streams. Cora's ARMv7 Linux
and ordinary x86 GNU Radio hosts are little-endian, so no byte-swapping block
is needed. Version 1 has no sample-rate or metadata framing: configure the
rate at each end explicitly.

## Start the board service

Boot the Cora image, connect its Ethernet cable to the PC network configured
as `192.168.10.1/24`, then SSH to the board and run:

```sh
cora-remote-io
```

The new image also provides an authenticated dashboard at
`http://192.168.10.2:8080/`. Its **Start Remote I/O** and **Stop Remote I/O**
buttons manage the same service. Read the configured dashboard password with
`cat /var/lib/cora-dashboard/token`; the development-image login is username
`cora` with password `root`.

It listens on both ports. The ADC endpoint always remains available and
requests 1,000 XADC reads per second by default. The 10 Hz rate used by
`cora-adc-smoke` is only its terminal redraw rate; it does not limit Remote
I/O acquisition.
The PWM endpoint waits for one PC client. If that client disconnects, its board
flowgraph stops, which stops the PWM DMA and disables the output before the
listener is restarted.

The corrected image auto-detects A0 as `voltage9` and uses the proper divider
gain. Older images that expose only `voltage8` have not registered VAUX1;
`voltage8` is VP/VN and cannot be used as an A0 workaround.

## PC GNU Radio Companion graph

The ready-to-open PC graph is
[`cora_remote_io_pc.grc`](../software/cora-pwm/gnuradio/gr-cora/examples/cora_remote_io_pc.grc).
It displays A0 and provides a PWM-level slider. Open it after installing the
host `gr-cora` package:

```sh
gnuradio-companion software/cora-pwm/gnuradio/gr-cora/examples/cora_remote_io_pc.grc
```

To build your own graph, add **Network TCP Source** and set:

- Item size: `4`
- Address: `192.168.10.2`
- Port: `50000`
- Server: disabled (the PC is the client)

Its output is a float stream in the common Cora range: `-1.0` is 0 V at A0,
`0.0` is about 1.65 V, and `+1.0` is 3.3 V. Connect it to a QT GUI Time Sink,
a probe, or the rest of the PC flowgraph. Set the graph sample rate to `1e3`
by default, matching the board service's ADC stream. Change both the PC graph
and `cora-remote-io --adc-sample-rate` together when using another rate.

For PC control of JA1 PWM, install the `gr-cora` package on the PC and add
**Cora TCP Float Sink** in GNU Radio Companion. (The board image already
installs it.) Its settings are:

- Item size: `4`
- Vector length: `1`
- Address: `192.168.10.2`
- Port: `50001`

Build and install the host package from the repository before using that GRC
block:

```sh
cmake -S software/cora-pwm/gnuradio/gr-cora -B /tmp/gr-cora-host
cmake --build /tmp/gr-cora-host
sudo cmake --install /tmp/gr-cora-host
```

Feed it a normalized float stream. The default PWM carrier is 100 kHz, so the
PC source should continuously produce 100,000 samples per second for a smooth
continuous output. For example, use a Float Signal Source with sample rate
`100e3`, then connect it to the TCP Sink. Values below -1 and above +1 are
clamped by the PWM conversion path.

## Scope and next step

TCP makes the current XADC path and PWM hardware easy to use from a PC
flowgraph. The XADC service reads its sysfs attribute once per requested
sample, so 1 ksample/s is a practical starting point—not a claim of sustained
1 MSPS acquisition. The established `/dev/cora-adc0` DMA ABI is the path for
the higher continuous rates of an external converter, with a timestamped and
framed transport added when that converter is selected.
