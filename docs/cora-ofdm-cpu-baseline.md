# Cora Z7-10 headless OFDM CPU baseline

`cora-ofdm` is the CPU-only, headless port of the underwater OFDM
demonstration. It deliberately leaves the FPGA design unchanged and does not
use the existing 512-point real FFT–mask–IFFT spectral accelerator. The
existing PWM, ADC, DMA, and spectral-accelerator components remain installed.

The application retains:

- 48 ksample/s complex baseband;
- a 256-point inverse/forward FFT and 64-sample cyclic prefix;
- QPSK payload symbols and six BPSK pilots;
- two known training symbols for synchronization, CFO estimation, and channel
  estimation;
- a four-path 1.27 ms shallow-water channel;
- AWGN, carrier-frequency offset, and Doppler/sample-rate scaling; and
- pilot-aided common-phase correction and one-tap equalization.

It contains no Qt, GUI ranges, GUI sinks, GRC runtime, SciPy, or plotting
dependency. GNU Radio performs the transmit IFFT, cyclic prefix, throttle, and
channel model. Target-friendly NumPy code performs Doppler correction,
synchronization, FFT-based receiver processing, equalization, QPSK decisions,
and bit-for-bit validation.

## Reproducible image build

From the PetaLinux project:

```sh
cd /home/eldden28/code/test-project/software/petalinux/cora-z7-10-baseline
source /tools/Xilinx/2025.1/PetaLinux/tool/settings.sh
petalinux-build -c cora-ofdm
petalinux-build
./package-wic.sh
sha256sum images/linux/petalinux-sdimage.wic
```

The recipe is
`project-spec/meta-user/recipes-radio/cora-ofdm/cora-ofdm.bb`. The package is
added through `project-spec/meta-user/conf/petalinuxbsp.conf`; no manual copy
to the root filesystem is needed.

The increment-4 checkpoint image generated on 2026-07-24 is:

```text
images/linux/petalinux-sdimage.wic
size: 2,684,358,656 bytes
SHA-256: c59f07494b4d9cf92d0329d2db421c7b962b6c328d5b1fc46a9fe831897ee14f
```

Its rootfs manifest contains `cora-ofdm`, `gnuradio-channels`, `cora-pwm`,
`cora-dsp`, `gr-cora`, and `cora-dashboard`. It contains no Qt or PyQt runtime
package. The packaged `/usr/bin/cora-ofdm` is byte-for-byte identical to the
increment-4 source at SHA-256
`4f3fa517b2244441fda9ba1ec4e647a32753470dbde7a476bf1fd52a8a20d535`.
The WIC partition table contains the intended 512 MiB FAT boot partition and
2 GiB Linux root partition. This build has not yet been flashed for a
persistent-image boot confirmation; target performance was measured by
staging the identical executable in `/tmp` on the preceding image.

## Target runs

The real-time run inserts a GNU Radio throttle at exactly 48 ksample/s:

```sh
cora-ofdm --mode realtime --packets 30 \
  --json-output /tmp/cora-ofdm-realtime.json
```

The benchmark removes the throttle and measures maximum end-to-end CPU
throughput:

```sh
cora-ofdm --mode benchmark --packets 100 \
  --json-output /tmp/cora-ofdm-benchmark.json
```

Packets are processed in bounded groups of 10 by default so the transmitter,
channel, and Doppler-correction graphs can be shared without unbounded memory
growth. Use `--batch-size 1` for the original packet-at-a-time behavior or
choose another positive batch size for profiling.

Both modes report:

- overall and processing wall time;
- process user-plus-system CPU time and CPU utilization;
- current and peak resident memory;
- requested, decoded, error-free, and erroneous packets;
- total bits, bit errors, and BER;
- simulated-sample and decoded-payload throughput; and
- throughput as a multiple of the 48 ksample/s real-time requirement.

One unmeasured warm-up packet runs by default before the statistics window.
This lets GNU Radio and VOLK perform one-time initialization and kernel
ranking without distorting the CPU baseline. Change it with
`--warmup-packets`; use zero only when measuring cold-start behavior.

The process exits with status 2 if a packet cannot be decoded. Payload
validation remains a separate reported PASS/FAIL result because the default
18 dB channel intentionally produces a measurable BER. Add
`--require-error-free` when a smoke or regression test should also return
status 2 for any mismatched decoded bit. Seeds advance by one per packet,
making a run deterministic but preventing every packet from carrying the same
data.

For a less verbose benchmark:

```sh
cora-ofdm --mode benchmark --packets 100 --quiet \
  --json-output /tmp/cora-ofdm-benchmark.json
```

The default impairment values match the original demonstration:

```text
SNR=18 dB, Doppler=900 ppm, CFO=5 Hz, payload=40 OFDM symbols
```

They may be overridden with `--snr-db`, `--doppler-ppm`, `--cfo-hz`, and
`--payload-symbols`.

## Optional PC visualization

Monitoring is disabled by default so it cannot affect baseline results.
Start the PC visualizer:

```sh
cd /home/eldden28/code/test-project
python3 software/cora-ofdm/tools/cora_ofdm_monitor.py --port 7355
```

Then send TX and RX complex64 samples from the board to the PC:

```sh
cora-ofdm --mode realtime --packets 100 \
  --monitor 192.168.10.1:7355
```

UDP datagrams contain a small `COFM` header followed by up to 1024
little-endian complex64 samples. Stream ID 0 is TX and stream ID 1 is RX.
Run official performance measurements without `--monitor`.

## Baseline result record

Record the board/image identity and retain both JSON outputs:

```sh
uname -a
cat /etc/os-release
sha256sum /boot/system.dtb 2>/dev/null || true
cora-ofdm --mode realtime --packets 30 \
  --json-output /tmp/cora-ofdm-realtime.json
cora-ofdm --mode benchmark --packets 100 --quiet \
  --json-output /tmp/cora-ofdm-benchmark.json
```

Copy the results to the build host:

```sh
scp petalinux@192.168.10.2:/tmp/cora-ofdm-*.json \
  /home/eldden28/code/test-project/results/
```

## Measured Cora Z7-10 baseline

The first CPU baseline was captured on 2026-07-23 from:

```text
Linux cora-z7-10-baseline 6.12.10-xilinx-g0a0f70e531c7
armv7l, two logical CPUs
GNU Radio v3.11.0.0git-1099-gcd20ee25
```

The currently booted baseline image did not yet contain `gr-channels`, so the
exact `gnuradio-channels` files from the newly built rootfs and the exact
`cora-ofdm` script were staged onto that board before measurement. The final
WIC contains both packages through Yocto. Repeat the same commands after
flashing to confirm installed-image equivalence.

Both runs used 10 measured packets after one unmeasured warm-up packet. Each
packet carried 12,160 payload bits in 40 OFDM payload symbols at the default
18 dB SNR, 900 ppm Doppler, and 5 Hz CFO.

| Metric | Benchmark | Real-time/throttled |
|---|---:|---:|
| Receiver status | PASS, 10/10 decoded | PASS, 10/10 decoded |
| Error-free packets | 0/10 | 0/10 |
| Bit errors / total bits | 992 / 121,600 | 992 / 121,600 |
| BER | 0.0081579 | 0.0081579 |
| Wall time | 39.621 s | 41.684 s |
| Process CPU time | 31.410 s | 31.440 s |
| Process CPU utilization | 79.3% | 75.4% |
| Current / peak RSS | 45,864 / 46,364 KiB | 46,540 / 46,636 KiB |
| Sample throughput | 4,251 sample/s | 4,020 sample/s |
| Real-time factor | 0.0886x | 0.0838x |
| Payload throughput | 3,231 bit/s | 3,056 bit/s |

The receiver successfully decoded every packet, while the intentionally noisy
18 dB channel produced a measurable BER and therefore no bit-exact packets.
More importantly, the unthrottled end-to-end implementation reaches only
8.86% of the required 48 ksample/s rate. The throttle is therefore not the
limiting factor; this packet-at-a-time Python/NumPy/GNU Radio CPU baseline
cannot run in real time on the Zynq-7010.

The preserved machine-readable results are:

- `results/cora-ofdm-benchmark.json`, SHA-256
  `a1317e15c5cb3dcbcdb31a0255bacce16f2940ffd3416010729e16e40be29be4`
- `results/cora-ofdm-realtime.json`, SHA-256
  `2f1ac81ecb3b7bb9340f1cd2e98f6b62129955bf23d30a3b071564ba74d2708b`

## Increment 1: cache Doppler-resampler taps

Target profiling showed that the multipath, AWGN, and CFO channel model used
only about 1% of packet CPU time. The main cost was constructing the Doppler
correction resampler: `pfb.arb_resampler_ccf` redesigned the same 819-tap
low-pass filter for every packet. One tap design took about 2.15 seconds of
CPU time on the Cora, while running an already-constructed resampler took
about 0.028 seconds.

The first low-risk optimization caches the tap tuple by resampling rate and
passes it into each resampler instance. It does not change the resampling
rate, filter coefficients, channel, receiver, or validation path. A unit test
checks that repeated requests reuse the cached tap design. All five host tests
pass, and `petalinux-build -c cora-ofdm` completes successfully.

For an exact comparison, the optimized executable was staged in `/tmp` on the
same running board. Both modes again processed 10 measured packets after one
warm-up packet:

| Metric | Baseline benchmark | Cached-tap benchmark | Baseline real-time | Cached-tap real-time |
|---|---:|---:|---:|---:|
| Receiver status | 10/10 decoded | 10/10 decoded | 10/10 decoded | 10/10 decoded |
| Bit errors / total bits | 992 / 121,600 | 992 / 121,600 | 992 / 121,600 | 992 / 121,600 |
| BER | 0.0081579 | 0.0081579 | 0.0081579 | 0.0081579 |
| Wall time | 39.621 s | 12.489 s | 41.684 s | 14.745 s |
| Process CPU time | 31.410 s | 9.890 s | 31.440 s | 9.890 s |
| Process CPU utilization | 79.3% | 79.2% | 75.4% | 67.1% |
| Peak RSS | 46,364 KiB | 46,896 KiB | 46,636 KiB | 46,856 KiB |
| Sample throughput | 4,251 sample/s | 15,316 sample/s | 4,020 sample/s | 12,440 sample/s |
| Real-time factor | 0.0886x | 0.3191x | 0.0838x | 0.2592x |
| Payload throughput | 3,231 bit/s | 11,640 bit/s | 3,056 bit/s | 9,454 bit/s |

The benchmark is 3.60 times faster, and its CPU time is 68.5% lower, with
identical decoded bits and BER. This confirms that repeated filter design was
a major setup bottleneck. The implementation is still only 0.319 times real
time, so caching alone does not meet 48 ksample/s.

The increment-1 source snapshot is
`software/cora-ofdm/cora_ofdm.py`, SHA-256
`40e63243cdecc8265d083ed1365110ccee567d14c00b8a65a698cb9338487867`.
The checkpoint results are:

- `results/cora-ofdm-benchmark-tap-cache.json`, SHA-256
  `be9edfb157b85f51f80656064c516499ad7d7b23620370d61af7badcf9c82d26`
- `results/cora-ofdm-realtime-tap-cache.json`, SHA-256
  `615cedc2c71be43801d617139e907d02c4bfb96dca51b59f10204be120243504`

## Increment 2: bounded packet batching

The second optimization sends up to 10 complete guarded packets through one
GNU Radio transmitter/channel graph and one Doppler-correction graph.
Synchronization, CFO estimation, channel estimation, equalization, QPSK
decisions, and bit validation still run independently for every packet.
One-symbol overlapping windows around the known guarded packet boundaries let
each synchronizer find its own training symbols after continuous channel and
resampler operation.

`--batch-size 1` is the compatibility control. On the Cora it reproduced the
increment-1 result exactly: 10/10 packets decoded, 992 bit errors, and BER
0.0081579. With `--batch-size 10`, the same channel model uses one continuous,
deterministic noise stream across the batch, so the exact noise samples and
bit-error count change while the impairment parameters and validation
criteria remain unchanged.

| Metric | Cached taps, batch 1 | Batch 10 benchmark | Batch 10 real-time |
|---|---:|---:|---:|
| Receiver status | 10/10 decoded | 10/10 decoded | 10/10 decoded |
| Error-free packets | 0/10 | 0/10 | 0/10 |
| Bit errors / total bits | 992 / 121,600 | 951 / 121,600 | 951 / 121,600 |
| BER | 0.0081579 | 0.0078207 | 0.0078207 |
| Wall time | 12.407 s | 10.220 s | 13.057 s |
| Processing wall time | 10.348 s | 8.212 s | 11.143 s |
| Process CPU time | 9.890 s | 8.290 s | 8.370 s |
| Process CPU utilization | 79.7% | 81.1% | 64.1% |
| Peak RSS | 47,108 KiB | 59,688 KiB | 59,816 KiB |
| Sample throughput | 15,462 sample/s | 19,484 sample/s | 14,359 sample/s |
| Real-time factor | 0.3221x | 0.4059x | 0.2992x |
| Payload throughput | 11,751 bit/s | 14,807 bit/s | 10,913 bit/s |

Relative to the increment-1 benchmark, batch 10 raises processing throughput
by 27.2%, reduces process CPU time by 16.2%, and reduces total wall time by
18.2%. Peak RSS rises by 12,792 KiB. This is a useful but bounded improvement:
the unthrottled implementation now reaches 40.6% of the 48 ksample/s target.

A warmed 10-packet target profile attributed the remaining execution time as
follows:

| Phase | Wall time | CPU time |
|---|---:|---:|
| Packet generation | 1.753 s | 1.450 s |
| TX reference generation | 0.091 s | 0.080 s |
| Shared TX/channel graph build | 0.726 s | 0.630 s |
| Shared TX/channel graph run | 0.420 s | 0.440 s |
| Shared Doppler correction | 1.392 s | 1.160 s |
| Per-packet synchronization | 1.891 s | 1.490 s |
| Receiver including synchronization | 5.471 s | 4.300 s |

The receiver remainder after subtracting synchronization is about 3.58 seconds
of wall time. Its current payload loop performs 40 separate 256-point NumPy
FFTs per packet, or 400 calls per 10-packet batch. Vectorizing those payload
FFTs and the associated equalization is therefore the next incremental
optimization; synchronization should be addressed separately afterward.

The increment-2 source is `software/cora-ofdm/cora_ofdm.py`, SHA-256
`ae729fde9e178fbbbcd046de20168ac0857af2e3047f19d2873ee7a6b20304e6`.
All six host tests and `petalinux-build -c cora-ofdm` pass. The checkpoint
results are:

- `results/cora-ofdm-benchmark-batch1-control.json`, SHA-256
  `597371fdd15a5b5168c2025d1c63d14d0bbe58fa8ee2e7f4609643f24da34f8b`
- `results/cora-ofdm-benchmark-batch10.json`, SHA-256
  `72914c178d649f446167c6d5be799961dbf226b04bcee0a36bdc8ffcd1d7aecc`
- `results/cora-ofdm-realtime-batch10.json`, SHA-256
  `b9b242609f4f25879ea88e8934ad5acd288ac1fa4c6b529a043a304c38374e75`

## Increment 3: vectorized receiver FFT and equalization

The third optimization presents the useful samples from all payload symbols
in a packet as a read-only strided matrix. One NumPy FFT call now transforms
the full 40-by-256 matrix, and array operations perform channel equalization,
pilot phase estimation, phase correction, and data-bin extraction. The two
training FFTs are similarly combined into one matrix transform.

This reduces the payload receiver from 40 small FFT calls per packet to one,
or from 400 calls to 10 per measured batch. The strided input view does not
copy sample data. A regression test verifies that it is identical to scalar
symbol extraction and is not writable.

Both target controls preserved the exact increment-2 receiver output:

- batch size 1: 992 bit errors, BER 0.0081579;
- batch size 10: 951 bit errors, BER 0.0078207; and
- every packet decoded in benchmark and real-time modes.

| Metric | Increment 2 benchmark | Vectorized benchmark | Vectorized real-time |
|---|---:|---:|---:|
| Receiver status | 10/10 decoded | 10/10 decoded | 10/10 decoded |
| Error-free packets | 0/10 | 0/10 | 0/10 |
| Bit errors / total bits | 951 / 121,600 | 951 / 121,600 | 951 / 121,600 |
| BER | 0.0078207 | 0.0078207 | 0.0078207 |
| Wall time | 10.220 s | 7.061 s | 10.079 s |
| Processing wall time | 8.212 s | 5.206 s | 8.184 s |
| Process CPU time | 8.290 s | 5.910 s | 6.010 s |
| Process CPU utilization | 81.1% | 83.7% | 59.6% |
| Peak RSS | 59,688 KiB | 59,348 KiB | 59,796 KiB |
| Sample throughput | 19,484 sample/s | 30,733 sample/s | 19,551 sample/s |
| Real-time factor | 0.4059x | 0.6403x | 0.4073x |
| Payload throughput | 14,807 bit/s | 23,357 bit/s | 14,859 bit/s |

Vectorization raises benchmark throughput by 57.7% over increment 2, reduces
CPU time by 28.7%, and slightly reduces peak memory. Relative to the original
CPU baseline, the benchmark is now 7.23 times faster and CPU time is 81.2%
lower. It reaches 64.0% of the required 48 ksample/s.

The warmed target profile confirms that receiver time including
synchronization fell from 5.471 to 2.380 seconds per 10 packets. Receiver time
after subtracting synchronization fell from about 3.58 to 0.55 seconds. The
new dominant phases are:

| Phase | Wall time | CPU time |
|---|---:|---:|
| Packet generation | 1.768 s | 1.460 s |
| TX reference generation | 0.103 s | 0.080 s |
| Shared TX/channel graph build | 0.824 s | 0.630 s |
| Shared TX/channel graph run | 0.421 s | 0.440 s |
| Shared Doppler correction | 1.446 s | 1.170 s |
| Per-packet synchronization | 1.832 s | 1.500 s |
| Receiver including synchronization | 2.380 s | 1.930 s |

Synchronization is now the largest measured processing phase. Packet
generation is nearly as large in total wall time, although it is intentionally
outside the processing-throughput interval for compatibility with the
original baseline. The next increment should compare the existing direct
normalized correlation with an FFT-based correlation while requiring
identical packet-start decisions.

The increment-3 source is `software/cora-ofdm/cora_ofdm.py`, SHA-256
`19abb5586bda24015dba0fe01d5c6865717a76d00591d2ae4cebc7014f5a1c99`.
All seven host tests and `petalinux-build -c cora-ofdm` pass. The checkpoint
results are:

- `results/cora-ofdm-benchmark-vector-batch1-control.json`, SHA-256
  `7b04e01fa225e8ad97c18210d58f513a2f3d830adf5818ab5545bb3714021804`
- `results/cora-ofdm-benchmark-vector.json`, SHA-256
  `fdbb07102e569e27d24729fdd6def63a3df7c1377b50eb5ec8ac15e82564c51d`
- `results/cora-ofdm-realtime-vector.json`, SHA-256
  `4f2134ca3f9892e9e7c665f47a0dc7ae9e0c1b0d82037690176dbe5938735f11`

## Increment 4: FFT-based synchronization correlation

The fourth optimization replaces NumPy's direct complex correlation with
frequency-domain convolution at the next power-of-two transform length.
Window-energy normalization, the detection metric, and the previous-training
symbol check remain unchanged. The direct implementation remains available
as `normalized_sync_direct` and serves as the regression oracle.

On the desktop host the direct implementation is faster, but the ARM target
has the opposite result. A side-by-side target test over all 10 packet windows
showed that the FFT method:

- selected the exact same packet-start sample every time;
- differed in normalized metric values by no more than about 2.3e-7; and
- reduced total synchronization wall time from 1.832 to 1.556 seconds.

The full receiver output is unchanged in both batch controls:

- batch size 1: 992 bit errors, BER 0.0081579;
- batch size 10: 951 bit errors, BER 0.0078207; and
- 10/10 packets decoded in every measured run.

| Metric | Increment 3 benchmark | FFT-sync benchmark | FFT-sync real-time |
|---|---:|---:|---:|
| Receiver status | 10/10 decoded | 10/10 decoded | 10/10 decoded |
| Error-free packets | 0/10 | 0/10 | 0/10 |
| Bit errors / total bits | 951 / 121,600 | 951 / 121,600 | 951 / 121,600 |
| BER | 0.0078207 | 0.0078207 | 0.0078207 |
| Wall time | 7.061 s | 7.016 s | 9.762 s |
| Processing wall time | 5.206 s | 5.019 s | 7.948 s |
| Process CPU time | 5.910 s | 5.760 s | 5.830 s |
| Process CPU utilization | 83.7% | 82.1% | 59.7% |
| Peak RSS | 59,348 KiB | 59,928 KiB | 59,732 KiB |
| Sample throughput | 30,733 sample/s | 31,876 sample/s | 20,130 sample/s |
| Real-time factor | 0.6403x | 0.6641x | 0.4194x |
| Payload throughput | 23,357 bit/s | 24,226 bit/s | 15,299 bit/s |

This is a smaller but valid target-specific gain: benchmark throughput rises
3.7%, CPU time falls 2.5%, and peak RSS increases by 580 KiB. Relative to the
original baseline, the implementation is now 7.50 times faster and uses 81.7%
less process CPU time.

The warmed 10-packet target profile is now:

| Phase | Wall time | CPU time |
|---|---:|---:|
| Packet generation | 1.821 s | 1.460 s |
| TX reference generation | 0.092 s | 0.090 s |
| Shared TX/channel graph build | 0.792 s | 0.630 s |
| Shared TX/channel graph run | 0.444 s | 0.430 s |
| Shared Doppler correction | 1.437 s | 1.170 s |
| FFT-based synchronization | 1.556 s | 1.290 s |
| Receiver including synchronization | 2.112 s | 1.740 s |

Packet generation is now the largest end-to-end phase, but it remains outside
the historical processing-throughput interval. Vectorizing its 40-symbol
Python loop is the next low-risk increment and will improve total wall time.
Within the processing interval, shared Doppler correction is the next major
target; graph fusion should be evaluated after packet generation.

The increment-4 source is `software/cora-ofdm/cora_ofdm.py`, SHA-256
`4f3fa517b2244441fda9ba1ec4e647a32753470dbde7a476bf1fd52a8a20d535`.
All eight host tests and `petalinux-build -c cora-ofdm` pass. The checkpoint
results are:

- `results/cora-ofdm-benchmark-fft-sync-batch1-control.json`, SHA-256
  `e9503ce4361f50a278deb3c5d5ba7435779184966cf89711bfe08b5c0d354939`
- `results/cora-ofdm-benchmark-fft-sync.json`, SHA-256
  `bc564f984ffff4be91943c6c7237f719bd4c933d83c64d62bccd469d8d5da532`
- `results/cora-ofdm-realtime-fft-sync.json`, SHA-256
  `00741b4daf421c7d409753437f78de102cf05148a078066960b5d4c0aaf5a8df`

Do not begin an FPGA OFDM implementation until these CPU measurements have
been captured. The implemented accelerator is a fixed 512-point real
FFT–mask–IFFT chain, whereas this modem requires independent 256-point complex
transforms. The implemented FPGA is also already at 96.2% slice utilization,
so later acceleration needs a separate hardware platform or an intentional
replacement/reconfiguration of existing logic.
