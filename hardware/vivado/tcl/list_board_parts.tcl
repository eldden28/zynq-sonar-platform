if {$argc < 1 || $argc > 2} {
    puts stderr "Usage: vivado -mode batch -source list_board_parts.tcl -tclargs <board-repository> ?pattern?"
    exit 64
}

set board_repository [file normalize [lindex $argv 0]]
set pattern "*"
if {$argc == 2} {
    set pattern [lindex $argv 1]
}

if {![file isdirectory $board_repository]} {
    puts stderr "ERROR: board repository does not exist: $board_repository"
    exit 2
}

set_param board.repoPaths [list $board_repository]
set matches [get_board_parts -quiet $pattern]
if {[llength $matches] == 0} {
    puts stderr "ERROR: no board parts matched '$pattern'"
    exit 3
}

foreach board $matches {
    puts "BOARD_PART=$board DEVICE_PART=[get_property PART_NAME $board]"
}
exit 0
