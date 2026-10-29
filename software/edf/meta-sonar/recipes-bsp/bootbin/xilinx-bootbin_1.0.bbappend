# Removing EDF's unsupported optional header leaves whitespace in the variable.
# The vendor parser treats that as a filename rather than an empty setting.
python do_configure:prepend:cora-z7-10() {
    d.setVar("BIF_OPTIONAL_DATA", (d.getVar("BIF_OPTIONAL_DATA") or "").strip())
}
