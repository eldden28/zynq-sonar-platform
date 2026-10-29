# Mirror HEAD requests need little CPU/RAM and should not share the small
# compilation-task limit on an 8 GiB workstation.
SONAR_SSTATE_MIRROR_THREADS ?= "16"
BB_HASHCHECK_FUNCTION:cora-z7-10 = "sonar_sstate_checkhashes"

def sonar_sstate_checkhashes(sq_data, d, siginfo=False, currentcount=0, summary=True, **kwargs):
    lookup_data = bb.data.createCopy(d)
    lookup_data.setVar("BB_NUMBER_THREADS", d.getVar("SONAR_SSTATE_MIRROR_THREADS"))
    bb.note("Sstate mirror lookup: %s workers; compilation task limit remains %s" %
            (lookup_data.getVar("BB_NUMBER_THREADS"), d.getVar("BB_NUMBER_THREADS")))
    return sstate_checkhashes(sq_data, lookup_data, siginfo=siginfo,
                             currentcount=currentcount, summary=summary, **kwargs)
