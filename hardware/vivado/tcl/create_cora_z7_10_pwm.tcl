set script_path [file normalize [info script]]
set repository_root [file normalize [file join [file dirname $script_path] .. .. ..]]
set board_repository [file join $repository_root third_party digilent-vivado-boards new board_files]
set output_directory [file join $repository_root build vivado cora-z7-10-pwm]
set export_directory [file join $repository_root hardware export cora-z7-10-pwm]
set report_directory [file join $repository_root build reports cora-z7-10-pwm]
set ip_project_directory [file join $output_directory ip_packager]
set ip_repository [file join $output_directory ip_repository]
set pwm_ip_directory [file join $ip_repository axis_pwm_s16]
set spectral_filter_ip_directory [file join $ip_repository axis_spectral_filter]

set build_mode build
if {$argc > 1} {
    puts stderr "Usage: create_cora_z7_10_pwm.tcl ?validate?"
    exit 64
}
if {$argc == 1} {
    set build_mode [lindex $argv 0]
    if {$build_mode ne "validate"} {
        puts stderr "ERROR: mode must be validate when supplied"
        exit 64
    }
}

set project_name cora_z7_10_pwm
set design_name system
set board_part digilentinc.com:cora-z7-10:part0:1.1
set device_part xc7z010clg400-1

set pwm_core_file [file join $repository_root hardware rtl axis_pwm_s16 axis_pwm_s16.sv]
set pwm_axi_file [file join $repository_root hardware rtl axis_pwm_s16 axis_pwm_s16_axi.sv]
set spectral_filter_core_file [file join $repository_root hardware rtl axis_spectral_filter axis_spectral_filter.sv]
set spectral_filter_axi_file [file join $repository_root hardware rtl axis_spectral_filter axis_spectral_filter_axi.sv]
set constraint_file [file join $repository_root hardware constraints cora-z7-10-axis-pwm.xdc]

if {![file isdirectory $board_repository]} {
    puts stderr "ERROR: Digilent board repository is missing: $board_repository"
    puts stderr "Run scripts/fetch-board-files.sh first."
    exit 2
}

file mkdir $output_directory
file mkdir $export_directory
file mkdir $report_directory
set_param board.repoPaths [list $board_repository]

if {[llength [get_board_parts -quiet $board_part]] != 1} {
    puts stderr "ERROR: Vivado cannot find board part $board_part"
    exit 3
}

# Package the tested SystemVerilog wrapper as a local IP. The AXI memory map is
# added explicitly so it is exported into the XSA and PetaLinux device tree.
file delete -force $ip_project_directory
file delete -force $ip_repository
create_project -force axis_pwm_s16_ip_packager $ip_project_directory -part $device_part
set_property target_language Verilog [current_project]
add_files -norecurse [list $pwm_core_file $pwm_axi_file]
set_property top axis_pwm_s16_axi [current_fileset]
update_compile_order -fileset sources_1

ipx::package_project -root_dir $pwm_ip_directory \
    -vendor eldden28.dev -library fpga -taxonomy /UserIP \
    -import_files -set_current true
set packaged_core [ipx::current_core]
set_property name axis_pwm_s16 $packaged_core
set_property display_name {Signed-16 AXI-stream PWM} $packaged_core
set_property description {Signed 16-bit AXI4-Stream to PWM sink with AXI4-Lite control} $packaged_core
set_property version 1.0 $packaged_core

set pwm_memory_map [ipx::add_memory_map S_AXI $packaged_core]
set pwm_address_block [ipx::add_address_block Reg $pwm_memory_map]
set_property range 65536 $pwm_address_block
set_property width 32 $pwm_address_block
set_property slave_memory_map_ref S_AXI \
    [ipx::get_bus_interfaces S_AXI -of_objects $packaged_core]
ipx::save_core $packaged_core
close_project

# Package the configurable frequency-domain bin mask used between the two
# Xilinx FFT cores.
create_project -force axis_spectral_filter_ip_packager $ip_project_directory -part $device_part
set_property target_language Verilog [current_project]
add_files -norecurse [list $spectral_filter_core_file $spectral_filter_axi_file]
set_property top axis_spectral_filter_axi [current_fileset]
update_compile_order -fileset sources_1

ipx::package_project -root_dir $spectral_filter_ip_directory \
    -vendor eldden28.dev -library fpga -taxonomy /UserIP \
    -import_files -set_current true
set packaged_core [ipx::current_core]
set_property name axis_spectral_filter $packaged_core
set_property display_name {512-bin AXI-stream spectral filter} $packaged_core
set_property description {Configurable symmetric frequency-bin mask for Q1.15 FFT streams} $packaged_core
set_property version 1.0 $packaged_core

set filter_memory_map [ipx::add_memory_map S_AXI $packaged_core]
set filter_address_block [ipx::add_address_block Reg $filter_memory_map]
set_property range 65536 $filter_address_block
set_property width 32 $filter_address_block
set_property slave_memory_map_ref S_AXI \
    [ipx::get_bus_interfaces S_AXI -of_objects $packaged_core]
ipx::save_core $packaged_core
close_project

create_project -force $project_name $output_directory -part $device_part
set_property board_part $board_part [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property ip_repo_paths [list $ip_repository] [current_project]
update_ip_catalog

add_files -fileset constrs_1 -norecurse $constraint_file

create_bd_design $design_name
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:* processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {apply_board_preset "1" make_external "FIXED_IO, DDR"} $ps7

set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000} \
    CONFIG.PCW_EN_CLK1_PORT {1} \
    CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {200.000000} \
] $ps7

set constant_zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* constant_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $constant_zero
set constant_one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* constant_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $constant_one

set reset_100 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* reset_100]
set reset_200 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* reset_200]
set reset_inverter [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:* reset_inverter]
set_property -dict [list CONFIG.C_SIZE {1} CONFIG.C_OPERATION {not}] $reset_inverter

connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] \
    [get_bd_pins reset_inverter/Op1]

foreach reset_cell [list $reset_100 $reset_200] {
    connect_bd_net [get_bd_pins reset_inverter/Res] \
        [get_bd_pins $reset_cell/ext_reset_in]
    connect_bd_net [get_bd_pins constant_zero/dout] \
        [get_bd_pins $reset_cell/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins constant_one/dout] \
        [get_bd_pins $reset_cell/aux_reset_in] \
        [get_bd_pins $reset_cell/dcm_locked]
}
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins reset_100/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] \
    [get_bd_pins reset_200/slowest_sync_clk]

set control_interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* control_interconnect]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {4}] $control_interconnect
set data_interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* data_interconnect]
set_property -dict [list CONFIG.NUM_SI {4} CONFIG.NUM_MI {1}] $data_interconnect

set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:* axi_dma_0]
set_property -dict [list \
    CONFIG.c_include_sg {1} \
    CONFIG.c_include_mm2s {1} \
    CONFIG.c_include_s2mm {0} \
    CONFIG.c_include_mm2s_dre {1} \
    CONFIG.c_m_axi_mm2s_data_width {64} \
    CONFIG.c_m_axis_mm2s_tdata_width {16} \
    CONFIG.c_mm2s_burst_size {16} \
    CONFIG.c_sg_length_width {26} \
] $dma

set dsp_dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:* axi_dma_dsp]
set_property -dict [list \
    CONFIG.c_include_sg {0} \
    CONFIG.c_include_mm2s {1} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_include_mm2s_dre {1} \
    CONFIG.c_include_s2mm_dre {1} \
    CONFIG.c_m_axi_mm2s_data_width {64} \
    CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} \
    CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_mm2s_burst_size {16} \
    CONFIG.c_s2mm_burst_size {16} \
    CONFIG.c_sg_length_width {26} \
] $dsp_dma

set fft_forward [create_bd_cell -type ip -vlnv xilinx.com:ip:xfft:* fft_forward]
set fft_inverse [create_bd_cell -type ip -vlnv xilinx.com:ip:xfft:* fft_inverse]
foreach fft_core [list $fft_forward $fft_inverse] {
    set_property -dict [list \
        CONFIG.transform_length {512} \
        CONFIG.implementation_options {pipelined_streaming_io} \
        CONFIG.input_width {16} \
        CONFIG.phase_factor_width {16} \
        CONFIG.data_format {fixed_point} \
        CONFIG.scaling_options {scaled} \
        CONFIG.output_ordering {natural_order} \
        CONFIG.throttle_scheme {nonrealtime} \
        CONFIG.aresetn {true} \
        CONFIG.xk_index {false} \
        CONFIG.ovflo {false} \
        CONFIG.target_clock_frequency {100} \
    ] $fft_core
}

# Forward transform: shift a total of nine bits (2+2+2+2+1), so its
# fixed-point output is FFT(x)/512. The inverse transform applies no shifts;
# together the pair has approximately unity gain.
set fft_forward_config [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* fft_forward_config]
set_property -dict [list CONFIG.CONST_WIDTH {16} CONFIG.CONST_VAL {0x0355}] $fft_forward_config
set fft_inverse_config [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* fft_inverse_config]
set_property -dict [list CONFIG.CONST_WIDTH {16} CONFIG.CONST_VAL {0x0000}] $fft_inverse_config

set spectral_filter [create_bd_cell -type ip -vlnv eldden28.dev:fpga:axis_spectral_filter:1.0 axis_spectral_filter_0]

set stream_clock_converter [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_clock_converter:* stream_clock_converter]
set_property -dict [list \
    CONFIG.IS_ACLK_ASYNC {1} \
    CONFIG.TDATA_NUM_BYTES {2} \
    CONFIG.HAS_TLAST {1} \
    CONFIG.HAS_TKEEP {0} \
] $stream_clock_converter

set control_clock_converter [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_clock_converter:* control_clock_converter]
set_property -dict [list \
    CONFIG.PROTOCOL {AXI4LITE} \
    CONFIG.ACLK_ASYNC {1} \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.ADDR_WIDTH {32} \
] $control_clock_converter

set pwm [create_bd_cell -type ip -vlnv eldden28.dev:fpga:axis_pwm_s16:1.0 axis_pwm_s16_0]
set_property -dict [list \
    CONFIG.DEFAULT_PERIOD_TICKS {2000} \
    CONFIG.FIFO_DEPTH {64} \
] $pwm

set interrupt_concat [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:* interrupt_concat]
set_property CONFIG.NUM_PORTS {3} $interrupt_concat

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] \
    [get_bd_intf_pins control_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M00_AXI] \
    [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M01_AXI] \
    [get_bd_intf_pins control_clock_converter/S_AXI]
connect_bd_intf_net [get_bd_intf_pins control_clock_converter/M_AXI] \
    [get_bd_intf_pins axis_pwm_s16_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M02_AXI] \
    [get_bd_intf_pins axi_dma_dsp/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M03_AXI] \
    [get_bd_intf_pins axis_spectral_filter_0/S_AXI]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] \
    [get_bd_intf_pins data_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_SG] \
    [get_bd_intf_pins data_interconnect/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins data_interconnect/M00_AXI] \
    [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dsp/M_AXI_MM2S] \
    [get_bd_intf_pins data_interconnect/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dsp/M_AXI_S2MM] \
    [get_bd_intf_pins data_interconnect/S03_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] \
    [get_bd_intf_pins stream_clock_converter/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins stream_clock_converter/M_AXIS] \
    [get_bd_intf_pins axis_pwm_s16_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dsp/M_AXIS_MM2S] \
    [get_bd_intf_pins fft_forward/S_AXIS_DATA]
connect_bd_intf_net [get_bd_intf_pins fft_forward/M_AXIS_DATA] \
    [get_bd_intf_pins axis_spectral_filter_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins axis_spectral_filter_0/M_AXIS] \
    [get_bd_intf_pins fft_inverse/S_AXIS_DATA]
connect_bd_intf_net [get_bd_intf_pins fft_inverse/M_AXIS_DATA] \
    [get_bd_intf_pins axi_dma_dsp/S_AXIS_S2MM]

connect_bd_net [get_bd_pins fft_forward_config/dout] \
    [get_bd_pins fft_forward/s_axis_config_tdata]
connect_bd_net [get_bd_pins fft_inverse_config/dout] \
    [get_bd_pins fft_inverse/s_axis_config_tdata]
connect_bd_net [get_bd_pins constant_one/dout] \
    [get_bd_pins fft_forward/s_axis_config_tvalid] \
    [get_bd_pins fft_inverse/s_axis_config_tvalid]

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK] \
    [get_bd_pins control_interconnect/aclk] \
    [get_bd_pins data_interconnect/aclk] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] \
    [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] \
    [get_bd_pins axi_dma_0/m_axi_sg_aclk] \
    [get_bd_pins axi_dma_dsp/s_axi_lite_aclk] \
    [get_bd_pins axi_dma_dsp/m_axi_mm2s_aclk] \
    [get_bd_pins axi_dma_dsp/m_axi_s2mm_aclk] \
    [get_bd_pins fft_forward/aclk] \
    [get_bd_pins fft_inverse/aclk] \
    [get_bd_pins axis_spectral_filter_0/aclk] \
    [get_bd_pins stream_clock_converter/s_axis_aclk] \
    [get_bd_pins control_clock_converter/s_axi_aclk]

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] \
    [get_bd_pins stream_clock_converter/m_axis_aclk] \
    [get_bd_pins control_clock_converter/m_axi_aclk] \
    [get_bd_pins axis_pwm_s16_0/aclk]

connect_bd_net [get_bd_pins reset_100/peripheral_aresetn] \
    [get_bd_pins control_interconnect/aresetn] \
    [get_bd_pins data_interconnect/aresetn] \
    [get_bd_pins axi_dma_0/axi_resetn] \
    [get_bd_pins axi_dma_dsp/axi_resetn] \
    [get_bd_pins fft_forward/aresetn] \
    [get_bd_pins fft_inverse/aresetn] \
    [get_bd_pins axis_spectral_filter_0/aresetn] \
    [get_bd_pins stream_clock_converter/s_axis_aresetn] \
    [get_bd_pins control_clock_converter/s_axi_aresetn]
connect_bd_net [get_bd_pins reset_200/peripheral_aresetn] \
    [get_bd_pins stream_clock_converter/m_axis_aresetn] \
    [get_bd_pins control_clock_converter/m_axi_aresetn] \
    [get_bd_pins axis_pwm_s16_0/aresetn]

connect_bd_net [get_bd_pins axi_dma_0/mm2s_introut] \
    [get_bd_pins interrupt_concat/In0]
connect_bd_net [get_bd_pins axi_dma_dsp/mm2s_introut] \
    [get_bd_pins interrupt_concat/In1]
connect_bd_net [get_bd_pins axi_dma_dsp/s2mm_introut] \
    [get_bd_pins interrupt_concat/In2]
connect_bd_net [get_bd_pins interrupt_concat/dout] \
    [get_bd_pins processing_system7_0/IRQ_F2P]

set external_pwm_port [create_bd_port -dir O pwm_out]
connect_bd_net [get_bd_pins axis_pwm_s16_0/pwm_out] $external_pwm_port

assign_bd_address -offset 0x40400000 -range 64K \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
assign_bd_address -offset 0x43c00000 -range 64K \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axis_pwm_s16_0/S_AXI/Reg]
assign_bd_address -offset 0x40410000 -range 64K \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_dma_dsp/S_AXI_LITE/Reg]
assign_bd_address -offset 0x43c10000 -range 64K \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axis_spectral_filter_0/S_AXI/Reg]
assign_bd_address -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] \
    [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -target_address_space [get_bd_addr_spaces axi_dma_0/Data_SG] \
    [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -target_address_space [get_bd_addr_spaces axi_dma_dsp/Data_MM2S] \
    [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -target_address_space [get_bd_addr_spaces axi_dma_dsp/Data_S2MM] \
    [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]

validate_bd_design
save_bd_design

puts "DMA_CONTROL_BASE=0x40400000"
puts "DMA_SCATTER_GATHER=1"
puts "PWM_CONTROL_BASE=0x43c00000"
puts "PWM_OUTPUT=JA1/Y18/LVCMOS33"
puts "DSP_DMA_CONTROL_BASE=0x40410000"
puts "SPECTRAL_FILTER_CONTROL_BASE=0x43c10000"
puts "DSP_FFT_LENGTH=512"

if {$build_mode eq "validate"} {
    puts "PWM_BLOCK_DESIGN_VALID=1"
    exit 0
}

set bd_file [get_files ${design_name}.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
update_compile_order -fileset sources_1

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

set implementation_progress [get_property PROGRESS [get_runs impl_1]]
set implementation_status [get_property STATUS [get_runs impl_1]]
if {$implementation_progress ne "100%" ||
    ![string match "*write_bitstream Complete!*" $implementation_status]} {
    puts stderr "ERROR: implementation did not complete successfully"
    puts stderr "STATUS=$implementation_status"
    exit 4
}

open_run impl_1
report_utilization -file [file join $report_directory utilization.rpt]
report_timing_summary -delay_type min_max -max_paths 20 \
    -file [file join $report_directory timing.rpt]
report_bus_skew -file [file join $report_directory bus_skew.rpt]

set worst_setup_path [get_timing_paths -delay_type max -max_paths 1]
set worst_setup_slack [get_property SLACK $worst_setup_path]
if {$worst_setup_slack < 0.0} {
    puts stderr "ERROR: implemented design has negative setup slack: $worst_setup_slack ns"
    exit 5
}

set xsa_file [file join $export_directory cora-z7-10-pwm.xsa]
write_hw_platform -fixed -include_bit -force -file $xsa_file

puts "WORST_SETUP_SLACK_NS=$worst_setup_slack"
puts "PWM_HARDWARE_PLATFORM_OK=$xsa_file"
puts "BOARD_PART=$board_part"
puts "DEVICE_PART=$device_part"
exit 0
