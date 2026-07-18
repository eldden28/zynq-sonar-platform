set script_path [file normalize [info script]]
set repository_root [file normalize [file join [file dirname $script_path] .. .. ..]]
set board_repository [file join $repository_root third_party digilent-vivado-boards new board_files]
set output_directory [file join $repository_root build vivado cora-z7-10]

if {$argc > 1} {
    puts stderr "Usage: vivado -mode batch -source create_base_platform.tcl ?output-directory?"
    exit 64
}
if {$argc == 1} {
    set output_directory [file normalize [lindex $argv 0]]
}

set project_name "cora_z7_10_base"
set design_name "system"
set board_part "digilentinc.com:cora-z7-10:part0:1.1"
set device_part "xc7z010clg400-1"

if {![file isdirectory $board_repository]} {
    puts stderr "ERROR: Digilent board repository is missing: $board_repository"
    puts stderr "Run scripts/fetch-board-files.sh first."
    exit 2
}

set_param board.repoPaths [list $board_repository]
if {[llength [get_board_parts -quiet $board_part]] != 1} {
    puts stderr "ERROR: Vivado cannot find board part $board_part"
    exit 3
}

file mkdir $output_directory
create_project -force $project_name $output_directory -part $device_part
set_property board_part $board_part [current_project]
set_property target_language VHDL [current_project]
set_property simulator_language Mixed [current_project]

create_bd_design $design_name
set processing_system [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:* processing_system7_0]

apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {apply_board_preset "1" make_external "FIXED_IO, DDR" Master "Disable" Slave "Disable"} \
    $processing_system

# The Digilent preset enables M_AXI_GP0. This PS-only baseline has no PL AXI
# slave, so disable the unused master until the identity peripheral is added.
set_property CONFIG.PCW_USE_M_AXI_GP0 {0} $processing_system

validate_bd_design
save_bd_design

set bd_file [get_files "${design_name}.bd"]
generate_target all $bd_file
set wrapper_file [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper_file
update_compile_order -fileset sources_1

set xsa_directory [file join $repository_root hardware export cora-z7-10]
file mkdir $xsa_directory
set xsa_file [file join $xsa_directory "${project_name}.xsa"]
write_hw_platform -fixed -force -file $xsa_file

puts "BASE_PLATFORM_PROJECT=[file join $output_directory ${project_name}.xpr]"
puts "BASE_PLATFORM_XSA=$xsa_file"
puts "BOARD_PART=$board_part"
puts "DEVICE_PART=$device_part"
exit 0
