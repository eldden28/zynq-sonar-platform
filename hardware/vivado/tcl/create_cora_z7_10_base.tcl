set script_dir [file dirname [file normalize [info script]]]
set repository_root [file normalize [file join $script_dir ../../..]]
set build_dir [file join $repository_root build vivado cora-z7-10-base]
set export_dir [file join $repository_root hardware export]
set board_repository [file join $repository_root third_party digilent-vivado-boards new board_files]

set project_name cora_z7_10_base
set design_name system
set board_part digilentinc.com:cora-z7-10:part0:1.1
set device_part xc7z010clg400-1

if {![file isdirectory $board_repository]} {
    puts stderr "ERROR: Digilent board files are absent: $board_repository"
    puts stderr "Run scripts/fetch-board-files.sh first."
    exit 2
}

file mkdir $build_dir
file mkdir $export_dir
set_param board.repoPaths [list $board_repository]

if {[llength [get_board_parts -quiet $board_part]] != 1} {
    puts stderr "ERROR: Vivado cannot find board part $board_part"
    exit 3
}

create_project -force $project_name $build_dir -part $device_part
set_property board_part $board_part [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

create_bd_design $design_name
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:* processing_system7_0]

apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {apply_board_preset "1" make_external "FIXED_IO, DDR"} $ps7

# The board preset enables M_AXI_GP0. The bring-up platform contains no PL AXI
# peripherals, so disable the unused interface until the identity block and
# first accelerator are introduced.
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {0}] $ps7

validate_bd_design
save_bd_design

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

set xsa_path [file join $export_dir cora-z7-10-base.xsa]
write_hw_platform -fixed -include_bit -force -file $xsa_path

puts "HARDWARE_PLATFORM_OK=$xsa_path"
puts "BOARD_PART=$board_part"
puts "DEVICE_PART=$device_part"
exit 0
