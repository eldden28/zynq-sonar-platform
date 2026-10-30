require sonar-edf-minimal.bb
SUMMARY = "Sonar applications on the native EDF Linux stack"
IMAGE_INSTALL:append = " cora-dashboard cora-sonar cora-triplet-beamformer cora-navigation"
