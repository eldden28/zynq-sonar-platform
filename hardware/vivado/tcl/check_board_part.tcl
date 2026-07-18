if {$argc != 1} {
    puts stderr "Usage: vivado -mode batch -source check_board_part.tcl -tclargs <board-repository>"
    exit 64
}

set board_repository [file normalize [lindex $argv 0]]
set expected_board "digilentinc.com:cora-z7-10:part0:1.1"
set expected_part "xc7z010clg400-1"

if {![file isdirectory $board_repository]} {
    puts stderr "ERROR: board repository does not exist: $board_repository"
    exit 2
}

set_param board.repoPaths [list $board_repository]
set matches [get_board_parts -quiet $expected_board]

if {[llength $matches] != 1} {
    puts stderr "ERROR: expected board part '$expected_board' was not found"
    exit 3
}

set board [lindex $matches 0]
set detected_part [get_property PART_NAME $board]
if {$detected_part ne $expected_part} {
    puts stderr "ERROR: board part maps to '$detected_part', expected '$expected_part'"
    exit 4
}

puts "BOARD_PART_OK=$board"
puts "DEVICE_PART_OK=$detected_part"
puts "BOARD_REPOSITORY=$board_repository"
exit 0
