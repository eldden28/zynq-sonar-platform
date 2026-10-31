set demo_ready {/home/eldden28/code/test-project/docs/demo-videos/cora-vivado-block-design-live-narrated-take-02/vivado-ready}
set demo_trigger {/home/eldden28/code/test-project/docs/demo-videos/cora-vivado-block-design-live-narrated-take-02/record-trigger}
set demo_log {/home/eldden28/code/test-project/docs/demo-videos/cora-vivado-block-design-live-narrated-take-02/vivado-actions.log}
proc demo_append {message} {
  global demo_log
  set handle [open $demo_log a]
  puts $handle $message
  close $handle
}
proc demo_clear {} {
  catch {unhighlight_objects [get_bd_cells -quiet -hierarchical *]}
  catch {unselect_objects [get_bd_cells -quiet -hierarchical *]}
}
proc demo_overview {} {
  demo_clear
  demo_append OVERVIEW
}
proc demo_highlight {names} {
  demo_clear
  set objects [list]
  foreach name $names {
    set found [get_bd_cells -quiet $name]
    if {[llength $found] == 0} {demo_append [format {MISSING %s} $name]}
    foreach object $found {lappend objects $object}
  }
  if {[llength $objects] > 0} {
    catch {highlight_objects -color orange $objects}
    catch {select_objects $objects}
  }
  demo_append [format {HIGHLIGHT %s} [join $names ,]]
}
proc demo_start {} {
  global demo_trigger
  if {![file exists $demo_trigger]} {after 100 demo_start; return}
  demo_append START
  after 0 {demo_overview}
  after 2212 {demo_overview}
  after 22124 {demo_overview}
  after 25637 {demo_highlight [list {/axi_dma_dsp} {/data_interconnect}]}
  after 44077 {demo_overview}
  after 47276 {demo_highlight [list {/axis_acomms_router_0}]}
  after 64068 {demo_overview}
  after 66833 {demo_highlight [list {/fft_forward} {/axis_spectral_filter_0} {/fft_inverse}]}
  after 82501 {demo_overview}
  after 85532 {demo_highlight [list {/control_interconnect} {/reset_100} {/axis_acomms_router_0} {/axis_spectral_filter_0}]}
  after 102705 {demo_overview}
  after 105329 {demo_highlight [list {/axis_acomms_router_0} {/fft_forward} {/axis_spectral_filter_0} {/fft_inverse}]}
  after 124573 {demo_overview}
  after 126685 {demo_overview}
  after 146695 {demo_append COMPLETE}
}
open_project {/home/eldden28/code/test-project/build/vivado/cora-z7-10-pwm/cora_z7_10_pwm.xpr}
open_bd_design [get_files {/home/eldden28/code/test-project/build/vivado/cora-z7-10-pwm/cora_z7_10_pwm.srcs/sources_1/bd/system/system.bd}]
catch {regenerate_bd_layout}
catch {validate_bd_design}
set handle [open $demo_ready w]
puts $handle READY
close $handle
demo_start
demo_append GUI_READY
