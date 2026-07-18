# Cora Z7-10 SD image

The generated, ready-to-write image is:

`petalinux/cora-z7-10/images/linux/petalinux-sdimage.wic`

It is a 2.5 GiB raw disk image with a 512 MiB FAT32 boot partition and a
2 GiB ext4 root partition. Use an SD card of at least 4 GB. Writing the image
destroys all existing data on the selected device.

## Write the card

From the repository root, insert the SD card and identify its whole-device
path carefully:

```bash
lsblk -p -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS
```

Unmount any automatically mounted partitions, replacing `/dev/sdX1` and
`/dev/sdX2` with the paths shown by `lsblk`:

```bash
sudo umount /dev/sdX1 /dev/sdX2
```

Write to the whole card (for example `/dev/sdb`), not a partition such as
`/dev/sdb1`:

```bash
sudo dd if="$PWD/petalinux/cora-z7-10/images/linux/petalinux-sdimage.wic" of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

Before writing, the image can be checked with:

```bash
(cd petalinux/cora-z7-10 && sha256sum -c images/linux/SHA256SUMS)
```

## First boot

Set the Cora Z7-10 boot-mode jumper for SD boot according to the board's
reference manual, insert the card, connect the USB-UART port, and power on.
Use a serial terminal at 115200 baud, 8 data bits, no parity, and 1 stop bit.

This development image has root serial autologin, an empty root password,
OpenSSH, and a static Ethernet address of `192.168.10.2/24`. The directly
connected host should use `192.168.10.1/24`; the board uses that host address
as its gateway. Those settings are intentionally
insecure and must be changed before the system is placed on an untrusted
network or used as a production image.

For example, after identifying the host interface with `ip link`, configure
it temporarily (replace `enp3s0` with the actual interface name):

```bash
sudo ip address flush dev enp3s0
sudo ip address add 192.168.10.1/24 dev enp3s0
sudo ip link set enp3s0 up
ping 192.168.10.2
```

The image targets the Digilent Cora Z7-10 revision B board preset and contains
a minimal PS-only Vivado design. It includes the FPGA bitstream, FSBL, U-Boot,
Linux 6.12, the device tree, boot script, and persistent ext4 root filesystem.
GNU Radio and the future FPGA accelerator are not yet included in this first
hardware bring-up image.
