# Xilinx and SSH HIL training

- [Editable training guide](xilinx-ssh-hil-training-guide.md)
- [PDF handout](../../output/pdf/xilinx-ssh-hil-training-guide.pdf)
- [SSH loopback regression example](examples/run-hil-regression.sh)

The guide covers the project's actual Vivado/PetaLinux integration, SSH-based
hardware tests, accelerator verification, deployment, regression design,
automation, evidence capture, and troubleshooting. It distinguishes recorded
board results from proposed extensions.

The board was unreachable during preparation. The shell examples were
syntax-checked; the regression runner was not executed on hardware.

From the repository root, on a reserved and provisioned board:

```bash
bash docs/training/examples/run-hil-regression.sh
```

Rebuild the PDF with Python and ReportLab, using installed DejaVu Sans fonts:

```bash
python3 docs/training/build_training_pdf.py
```

On this workstation, the bundled workspace Python supplies ReportLab. Its
path can be obtained through the Codex workspace-dependency tool.
