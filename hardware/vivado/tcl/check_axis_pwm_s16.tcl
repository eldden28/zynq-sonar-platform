set script_path [file normalize [info script]]
set repository_root [file normalize [file join [file dirname $script_path] .. .. ..]]
set rtl_file [file join $repository_root hardware rtl axis_pwm_s16 axis_pwm_s16.sv]
set constraint_file [file join $repository_root hardware constraints cora-z7-10-axis-pwm.xdc]
set report_directory [file join $repository_root build reports axis_pwm_s16]
set device_part xc7z010clg400-1

file mkdir $report_directory
read_verilog -sv $rtl_file
synth_design -top axis_pwm_s16 -part $device_part -mode out_of_context
read_xdc $constraint_file

set pwm_port [get_ports pwm_out]
set pwm_package_pin [get_property PACKAGE_PIN $pwm_port]
set pwm_iostandard [get_property IOSTANDARD $pwm_port]
if {$pwm_package_pin ne "Y18" || $pwm_iostandard ne "LVCMOS33"} {
    puts stderr "ERROR: pwm_out is not assigned to Cora Z7-10 JA1"
    exit 5
}

create_clock -name pwm_clock -period 5.000 [get_ports aclk]
set_clock_uncertainty 0.200 [get_clocks pwm_clock]

report_utilization -file [file join $report_directory utilization.rpt]
report_timing_summary -delay_type max -max_paths 10 \
    -file [file join $report_directory timing.rpt]

set worst_path [get_timing_paths -delay_type max -max_paths 1]
if {[llength $worst_path] == 0} {
    puts stderr "ERROR: no timing path was available after synthesis"
    exit 3
}

set worst_slack [get_property SLACK $worst_path]
puts "DEVICE_PART=$device_part"
puts "CLOCK_PERIOD_NS=5.000"
puts "WORST_SLACK_NS=$worst_slack"
puts "REPORT_DIRECTORY=$report_directory"
puts "PWM_OUTPUT=JA1/$pwm_package_pin/$pwm_iostandard"

if {$worst_slack < 0.0} {
    puts stderr "ERROR: axis_pwm_s16 does not meet the 200 MHz timing target"
    exit 4
}

puts "AXIS_PWM_SYNTHESIS_OK=1"
exit 0
