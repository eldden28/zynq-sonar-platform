if {$argc != 1} {
    puts stderr "Usage: vivado -mode batch -source check_target_part.tcl -tclargs <part>"
    exit 64
}

set target_part [lindex $argv 0]
set matches [get_parts -quiet $target_part]

if {[llength $matches] != 1} {
    puts stderr "ERROR: target part '$target_part' is not installed or is ambiguous"
    exit 2
}

set part [lindex $matches 0]
puts "TARGET_PART_OK=[get_property NAME $part]"
puts "FAMILY=[get_property FAMILY $part]"
exit 0
