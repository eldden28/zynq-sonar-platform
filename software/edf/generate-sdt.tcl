# Run with the matching Vivado/Vitis 2025.1 sdtgen launcher.
if {$argc != 2} {
    error "usage: sdtgen generate-sdt.tcl <hardware.xsa> <new-output-directory>"
}
set xsa [file normalize [lindex $argv 0]]
set outdir [file normalize [lindex $argv 1]]
set_dt_param -xsa $xsa -dir $outdir
generate_sdt
