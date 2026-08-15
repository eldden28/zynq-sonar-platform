# Cora Z7-10 Pmod JA, signal pin 1.
#
# Pmod signal levels are 3.3 V. Connect pwm_out only to a compatible logic
# input or external buffer/gate driver; it is not a power output.
set_property -dict { \
    PACKAGE_PIN Y18 \
    IOSTANDARD LVCMOS33 \
    DRIVE 8 \
    SLEW SLOW \
} [get_ports pwm_out]
