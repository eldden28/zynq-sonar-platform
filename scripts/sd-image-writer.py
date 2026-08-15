#!/usr/bin/env python3
"""Small, safety-oriented GUI for writing a raw FPGA SD-card image."""

from __future__ import annotations

import json
import os
import queue
import re
import stat
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = (
    REPOSITORY_ROOT
    / "software/petalinux/cora-z7-10-baseline/images/linux/petalinux-sdimage.wic"
)


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def removable_disks() -> list[dict]:
    result = subprocess.run(
        [
            "lsblk", "--json", "--bytes", "--paths",
            "--output", "NAME,PATH,TYPE,SIZE,MODEL,TRAN,RM,MOUNTPOINTS",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    disks = []
    for item in json.loads(result.stdout).get("blockdevices", []):
        transport = (item.get("tran") or "").lower()
        if item.get("type") != "disk":
            continue
        if not (bool(item.get("rm")) or transport == "usb"):
            continue
        if int(item.get("size") or 0) <= 0:
            continue
        item["children"] = item.get("children") or []
        disks.append(item)
    return disks


class WriterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cora SD Image Writer")
        self.resizable(False, False)
        self.disks: dict[str, dict] = {}
        self.events: queue.Queue = queue.Queue()
        self.image_var = tk.StringVar(value=str(DEFAULT_IMAGE) if DEFAULT_IMAGE.exists() else "")
        self.device_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select an image and removable drive.")
        self._make_widgets()
        self.refresh_devices()
        self.after(100, self._poll_events)

    def _make_widgets(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.grid()
        ttk.Label(frame, text="Raw SD image").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.image_var, width=68).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(3, 10)
        )
        ttk.Button(frame, text="Browse…", command=self.choose_image).grid(row=1, column=2, padx=(8, 0))

        ttk.Label(frame, text="Removable target drive").grid(row=2, column=0, sticky="w")
        self.device_box = ttk.Combobox(
            frame, textvariable=self.device_var, width=58, state="readonly"
        )
        self.device_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 10))
        self.device_box.bind("<<ComboboxSelected>>", lambda _event: self.confirm_var.set(""))
        ttk.Button(frame, text="Refresh", command=self.refresh_devices).grid(row=3, column=2, padx=(8, 0))

        ttk.Label(
            frame,
            text="All data on the selected drive will be destroyed. Type ERASE to enable writing.",
            foreground="#a00000",
        ).grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Entry(frame, textvariable=self.confirm_var, width=18).grid(
            row=5, column=0, sticky="w", pady=(5, 10)
        )

        self.progress = ttk.Progressbar(frame, length=450, mode="determinate", maximum=100)
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        ttk.Label(frame, textvariable=self.status_var, wraplength=580).grid(
            row=7, column=0, columnspan=3, sticky="w"
        )
        self.write_button = ttk.Button(frame, text="Write SD card", command=self.start_write)
        self.write_button.grid(row=8, column=2, sticky="e", pady=(14, 0))

    def choose_image(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select raw SD image",
            filetypes=[("Raw disk images", "*.wic *.img"), ("All files", "*")],
        )
        if selected:
            self.image_var.set(selected)

    def refresh_devices(self) -> None:
        try:
            found = removable_disks()
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            messagebox.showerror("Drive discovery failed", str(exc))
            return
        self.disks.clear()
        labels = []
        for disk in found:
            path = disk.get("path") or disk.get("name")
            label = " — ".join(
                part for part in (
                    path,
                    human_size(int(disk.get("size") or 0)),
                    (disk.get("model") or "Unknown model").strip(),
                    (disk.get("tran") or "unknown transport").upper(),
                ) if part
            )
            labels.append(label)
            self.disks[label] = disk
        self.device_box["values"] = labels
        self.device_var.set(labels[0] if len(labels) == 1 else "")
        self.confirm_var.set("")
        self.status_var.set(
            f"Found {len(labels)} removable drive(s)." if labels else "No removable USB/SD drive found."
        )

    def start_write(self) -> None:
        image = Path(self.image_var.get()).expanduser().resolve()
        disk = self.disks.get(self.device_var.get())
        if not image.is_file():
            messagebox.showerror("Invalid image", "Select an existing .wic or .img file.")
            return
        if not disk:
            messagebox.showerror("No target", "Select a removable target drive.")
            return
        if self.confirm_var.get() != "ERASE":
            messagebox.showerror("Confirmation required", "Type ERASE exactly before writing.")
            return
        device = os.path.realpath(disk.get("path") or disk.get("name"))
        try:
            mode = os.stat(device).st_mode
        except OSError as exc:
            messagebox.showerror("Target unavailable", str(exc))
            return
        if not stat.S_ISBLK(mode):
            messagebox.showerror("Unsafe target", f"{device} is not a block device.")
            return
        image_size = image.stat().st_size
        disk_size = int(disk.get("size") or 0)
        if image_size > disk_size:
            messagebox.showerror(
                "Card too small",
                f"Image is {human_size(image_size)} but the drive is {human_size(disk_size)}.",
            )
            return
        if not messagebox.askyesno(
            "Final erase confirmation",
            f"Permanently erase {device} ({human_size(disk_size)}) and write:\n\n{image}\n\nContinue?",
            icon="warning",
        ):
            return
        self.write_button.state(["disabled"])
        self.progress["value"] = 0
        self.status_var.set("Unmounting partitions…")
        threading.Thread(
            target=self._write_worker, args=(image, device, disk, image_size), daemon=True
        ).start()

    def _write_worker(self, image: Path, device: str, disk: dict, image_size: int) -> None:
        try:
            for child in disk.get("children") or []:
                child_path = child.get("path") or child.get("name")
                if not child_path:
                    continue
                mounts = [m for m in (child.get("mountpoints") or []) if m]
                if mounts:
                    result = subprocess.run(
                        ["udisksctl", "unmount", "--block-device", child_path],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode:
                        raise RuntimeError(result.stderr.strip() or f"Could not unmount {child_path}")
            self.events.put(("status", "Waiting for authorization, then writing…"))
            process = subprocess.Popen(
                [
                    "pkexec", "dd", f"if={image}", f"of={device}",
                    "bs=4M", "status=progress", "conv=fsync",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stderr is not None
            for line in process.stderr:
                match = re.search(r"^\s*(\d+)\s+bytes", line)
                if match:
                    written = int(match.group(1))
                    self.events.put(("progress", min(100, written * 100 / image_size)))
                    self.events.put(("status", f"Writing: {human_size(written)} of {human_size(image_size)}"))
            return_code = process.wait()
            if return_code:
                raise RuntimeError("Writing failed or authorization was cancelled.")
            self.events.put(("progress", 100))
            self.events.put(("done", f"Image written successfully to {device}. You may remove the card."))
        except Exception as exc:  # Report worker failures in the GUI thread.
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.progress["value"] = value
                elif kind == "status":
                    self.status_var.set(value)
                elif kind == "done":
                    self.status_var.set(value)
                    self.write_button.state(["!disabled"])
                    self.confirm_var.set("")
                    messagebox.showinfo("Write complete", value)
                elif kind == "error":
                    self.status_var.set("Write did not complete.")
                    self.write_button.state(["!disabled"])
                    self.confirm_var.set("")
                    messagebox.showerror("Write failed", value)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


if __name__ == "__main__":
    WriterApp().mainloop()
