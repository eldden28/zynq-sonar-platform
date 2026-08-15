# Cora hardware dashboard

The dashboard is a dependency-light HTTP service for the Cora Z7 image. It
provides live system, Ethernet, FPGA, IIO and PWM telemetry; guarded service,
PWM and power controls; and a PTY-backed web shell running as `petalinux`.
Its ADC panel lists and plots raw 12-bit samples for all Cora user inputs:
A0-A5, A6-A7, A8-A9, A10-A11, and VP/VN. The chart is dependency-free and
runs locally in the browser, so the private board link needs no internet
access.

It listens on port 8080. HTTP Basic authentication uses username `cora`. The
development image seeds `/var/lib/cora-dashboard/token` with the password
`root`, so the login remains the same after rebuilding or reflashing the SD
card. The credential file is readable only by root.

After the board boots, the configured password can be checked over serial or
SSH:

```sh
cat /var/lib/cora-dashboard/token
```

Then browse to `http://192.168.10.2:8080/` and sign in as `cora` with password
`root`. The service
starts automatically. The dashboard can start and stop Cora Remote I/O, stop
PWM DMA safely, clear PWM statistics, reboot or power off the board, and open
up to two terminal sessions as the unprivileged `petalinux` user.

The service may be disabled or reconfigured in
`/etc/default/cora-dashboard`; its SysV init command is
`/etc/init.d/cora-dashboard {start|stop|restart|status}`.

This first version is intended for the private direct Ethernet link. It does
not provide TLS and must not be exposed directly to an untrusted network or
the public internet.
