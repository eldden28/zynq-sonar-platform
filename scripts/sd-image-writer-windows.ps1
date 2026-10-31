param(
    [string]$ImagePath = ""
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    try {
        $powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $arguments = '-NoProfile -STA -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath.Replace('"', '\"')
        if ($ImagePath) {
            $arguments += ' -ImagePath "{0}"' -f $ImagePath.Replace('"', '\"')
        }
        Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments | Out-Null
    }
    catch {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "Administrator access is required to write a raw SD-card image.`r`n`r`n$($_.Exception.Message)",
            "Cora SD Image Writer",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    exit
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$writerSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Concurrent;
using Microsoft.Win32.SafeHandles;

public sealed class CoraWriterUpdate
{
    public string Kind { get; private set; }
    public double Percent { get; private set; }
    public string Message { get; private set; }

    public CoraWriterUpdate(string kind, double percent, string message)
    {
        Kind = kind;
        Percent = percent;
        Message = message;
    }
}

public static class CoraRawDiskWriter
{
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_WRITE_THROUGH = 0x80000000;
    private const uint FSCTL_LOCK_VOLUME = 0x00090018;
    private const uint FSCTL_DISMOUNT_VOLUME = 0x00090020;
    private const uint FSCTL_UNLOCK_VOLUME = 0x0009001c;
    private const uint IOCTL_DISK_GET_LENGTH_INFO = 0x0007405c;
    private const uint IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140;
    private const int BUFFER_SIZE = 4 * 1024 * 1024;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle device,
        uint controlCode,
        IntPtr inBuffer,
        uint inBufferSize,
        IntPtr outBuffer,
        uint outBufferSize,
        out uint bytesReturned,
        IntPtr overlapped);

    [StructLayout(LayoutKind.Sequential)]
    private struct DiskLengthInformation
    {
        public long Length;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle device,
        uint controlCode,
        IntPtr inBuffer,
        uint inBufferSize,
        out DiskLengthInformation outBuffer,
        uint outBufferSize,
        out uint bytesReturned,
        IntPtr overlapped);

    [DllImport("msvcrt.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int memcmp(byte[] first, byte[] second, UIntPtr count);

    private static bool Control(SafeFileHandle handle, uint code)
    {
        uint ignored;
        return DeviceIoControl(
            handle, code, IntPtr.Zero, 0, IntPtr.Zero, 0, out ignored, IntPtr.Zero);
    }

    private static long DiskSize(SafeFileHandle handle)
    {
        DiskLengthInformation information;
        uint returned;
        if (!DeviceIoControl(
            handle,
            IOCTL_DISK_GET_LENGTH_INFO,
            IntPtr.Zero,
            0,
            out information,
            (uint)Marshal.SizeOf(typeof(DiskLengthInformation)),
            out returned,
            IntPtr.Zero))
        {
            throw new Win32Exception(
                Marshal.GetLastWin32Error(), "Could not determine the target disk size.");
        }
        return information.Length;
    }

    private static SafeFileHandle LockVolume(string letter)
    {
        string path = @"\\.\" + letter.Trim().TrimEnd(':') + ":";
        SafeFileHandle handle = CreateFile(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            IntPtr.Zero,
            OPEN_EXISTING,
            0,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, "Could not open volume " + letter + ":");
        }

        bool locked = false;
        for (int attempt = 0; attempt < 8 && !locked; attempt++)
        {
            locked = Control(handle, FSCTL_LOCK_VOLUME);
            if (!locked)
                Thread.Sleep(250);
        }
        if (!locked)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(
                error,
                "Could not lock volume " + letter +
                ":. Close File Explorer windows and applications using the card, then retry.");
        }
        if (!Control(handle, FSCTL_DISMOUNT_VOLUME))
        {
            int error = Marshal.GetLastWin32Error();
            Control(handle, FSCTL_UNLOCK_VOLUME);
            handle.Dispose();
            throw new Win32Exception(error, "Could not dismount volume " + letter + ":");
        }
        return handle;
    }

    private static void Report(
        ConcurrentQueue<CoraWriterUpdate> updates,
        string kind,
        double percent,
        string message)
    {
        updates.Enqueue(new CoraWriterUpdate(kind, percent, message));
    }

    private static string SizeText(long value)
    {
        double size = value;
        string[] units = { "B", "KiB", "MiB", "GiB", "TiB" };
        int unit = 0;
        while (size >= 1024.0 && unit < units.Length - 1)
        {
            size /= 1024.0;
            unit++;
        }
        return size.ToString("0.0") + " " + units[unit];
    }

    public static Task Start(
        string imagePath,
        int diskNumber,
        long expectedDiskSize,
        string[] volumeLetters,
        ConcurrentQueue<CoraWriterUpdate> updates)
    {
        return Task.Factory.StartNew(
            delegate
            {
                Write(imagePath, diskNumber, expectedDiskSize, volumeLetters, updates);
            },
            CancellationToken.None,
            TaskCreationOptions.LongRunning,
            TaskScheduler.Default);
    }

    private static void Write(
        string imagePath,
        int diskNumber,
        long expectedDiskSize,
        string[] volumeLetters,
        ConcurrentQueue<CoraWriterUpdate> updates)
    {
        List<SafeFileHandle> volumeHandles = new List<SafeFileHandle>();
        try
        {
            FileInfo imageInfo = new FileInfo(imagePath);
            if (!imageInfo.Exists || imageInfo.Length <= 0)
                throw new InvalidOperationException("The selected image is missing or empty.");

            Report(updates, "Progress", 0, "Locking and dismounting SD-card volumes...");
            if (volumeLetters != null)
            {
                foreach (string letter in volumeLetters)
                {
                    if (!String.IsNullOrWhiteSpace(letter))
                        volumeHandles.Add(LockVolume(letter));
                }
            }

            string diskPath = @"\\.\PhysicalDrive" + diskNumber;
            using (SafeFileHandle diskHandle = CreateFile(
                diskPath,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_WRITE_THROUGH,
                IntPtr.Zero))
            {
                if (diskHandle.IsInvalid)
                    throw new Win32Exception(
                        Marshal.GetLastWin32Error(), "Could not open " + diskPath + " for raw writing.");

                long actualDiskSize = DiskSize(diskHandle);
                if (actualDiskSize != expectedDiskSize)
                    throw new InvalidOperationException(
                        "The selected drive changed after confirmation. Refresh the drive list and retry.");
                if (imageInfo.Length > actualDiskSize)
                    throw new InvalidOperationException("The image is larger than the selected SD card.");

                using (FileStream image = new FileStream(
                    imagePath, FileMode.Open, FileAccess.Read, FileShare.Read, BUFFER_SIZE,
                    FileOptions.SequentialScan))
                using (FileStream disk = new FileStream(
                    diskHandle, FileAccess.ReadWrite, BUFFER_SIZE, false))
                {
                    byte[] buffer = new byte[BUFFER_SIZE];
                    long completed = 0;
                    Stopwatch reportTimer = Stopwatch.StartNew();
                    int count;
                    while ((count = image.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        disk.Write(buffer, 0, count);
                        completed += count;
                        if (reportTimer.ElapsedMilliseconds >= 200 || completed == imageInfo.Length)
                        {
                            Report(
                                updates,
                                "Progress",
                                completed * 100.0 / imageInfo.Length,
                                "Writing: " + SizeText(completed) + " of " + SizeText(imageInfo.Length));
                            reportTimer.Restart();
                        }
                    }
                    disk.Flush(true);

                    Report(updates, "Progress", 0, "Verifying the written image...");
                    image.Position = 0;
                    disk.Position = 0;
                    byte[] verifyBuffer = new byte[BUFFER_SIZE];
                    completed = 0;
                    reportTimer.Restart();
                    while ((count = image.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        int received = 0;
                        while (received < count)
                        {
                            int amount = disk.Read(verifyBuffer, received, count - received);
                            if (amount <= 0)
                                throw new IOException("The SD card ended unexpectedly during verification.");
                            received += amount;
                        }
                        if (memcmp(buffer, verifyBuffer, (UIntPtr)(uint)count) != 0)
                        {
                            long mismatch = completed;
                            for (int index = 0; index < count; index++)
                            {
                                if (buffer[index] != verifyBuffer[index])
                                {
                                    mismatch += index;
                                    break;
                                }
                            }
                            throw new IOException(
                                "Verification failed at image byte " + mismatch + ".");
                        }
                        completed += count;
                        if (reportTimer.ElapsedMilliseconds >= 200 || completed == imageInfo.Length)
                        {
                            Report(
                                updates,
                                "Progress",
                                completed * 100.0 / imageInfo.Length,
                                "Verifying: " + SizeText(completed) + " of " + SizeText(imageInfo.Length));
                            reportTimer.Restart();
                        }
                    }
                    Control(diskHandle, IOCTL_DISK_UPDATE_PROPERTIES);
                }
            }

            Report(
                updates,
                "Done",
                100,
                "The image was written and verified successfully. Safely eject the SD card.");
        }
        catch (Exception exception)
        {
            Report(updates, "Error", 0, exception.Message);
        }
        finally
        {
            for (int index = volumeHandles.Count - 1; index >= 0; index--)
            {
                try { Control(volumeHandles[index], FSCTL_UNLOCK_VOLUME); }
                catch { }
                volumeHandles[index].Dispose();
            }
        }
    }
}
'@

try {
    Add-Type -TypeDefinition $writerSource -Language CSharp
}
catch {
    [System.Windows.Forms.MessageBox]::Show(
        "The raw-disk writer could not be initialized.`r`n`r`n$($_.Exception.Message)",
        "Cora SD Image Writer",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

function Format-ByteSize([long]$Value) {
    $size = [double]$Value
    foreach ($unit in @("B", "KiB", "MiB", "GiB", "TiB")) {
        if (($size -lt 1024) -or ($unit -eq "TiB")) {
            return "{0:N1} {1}" -f $size, $unit
        }
        $size /= 1024
    }
}

function Get-SafeRemovableDisks {
    $result = @()
    foreach ($disk in @(Get-Disk -ErrorAction Stop)) {
        $bus = [string]$disk.BusType
        if ($bus -notin @("USB", "SD", "MMC")) { continue }
        if ([bool]$disk.IsBoot -or [bool]$disk.IsSystem) { continue }
        if ([long]$disk.Size -le 0) { continue }

        $letters = @(
            Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue |
                Where-Object { $null -ne $_.DriveLetter -and [string]$_.DriveLetter -ne "" } |
                ForEach-Object { [string]$_.DriveLetter }
        )
        $name = [string]$disk.FriendlyName
        if ([string]::IsNullOrWhiteSpace($name)) { $name = "Unknown device" }
        $label = "PhysicalDrive{0} - {1} - {2} - {3}" -f `
            $disk.Number, (Format-ByteSize ([long]$disk.Size)), $name.Trim(), $bus
        $result += [pscustomobject]@{
            Number = [int]$disk.Number
            Size = [long]$disk.Size
            Name = $name.Trim()
            Bus = $bus
            Letters = [string[]]$letters
            Label = $label
        }
    }
    return @($result | Sort-Object Number)
}

$script:diskMap = @{}
$script:writerActive = $false
$script:writeTask = $null
$script:updates = New-Object 'System.Collections.Concurrent.ConcurrentQueue[CoraWriterUpdate]'

$form = New-Object System.Windows.Forms.Form
$form.Text = "Cora SD Image Writer"
$form.ClientSize = New-Object System.Drawing.Size(700, 390)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$imageLabel = New-Object System.Windows.Forms.Label
$imageLabel.Location = New-Object System.Drawing.Point(18, 18)
$imageLabel.Size = New-Object System.Drawing.Size(300, 22)
$imageLabel.Text = "Raw SD image (.wic or .img)"
$form.Controls.Add($imageLabel)

$imageBox = New-Object System.Windows.Forms.TextBox
$imageBox.Location = New-Object System.Drawing.Point(18, 42)
$imageBox.Size = New-Object System.Drawing.Size(570, 24)
$form.Controls.Add($imageBox)

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Location = New-Object System.Drawing.Point(598, 40)
$browseButton.Size = New-Object System.Drawing.Size(84, 28)
$browseButton.Text = "Browse..."
$form.Controls.Add($browseButton)

$driveLabel = New-Object System.Windows.Forms.Label
$driveLabel.Location = New-Object System.Drawing.Point(18, 82)
$driveLabel.Size = New-Object System.Drawing.Size(300, 22)
$driveLabel.Text = "Removable target drive"
$form.Controls.Add($driveLabel)

$driveBox = New-Object System.Windows.Forms.ComboBox
$driveBox.Location = New-Object System.Drawing.Point(18, 106)
$driveBox.Size = New-Object System.Drawing.Size(570, 24)
$driveBox.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$form.Controls.Add($driveBox)

$refreshButton = New-Object System.Windows.Forms.Button
$refreshButton.Location = New-Object System.Drawing.Point(598, 104)
$refreshButton.Size = New-Object System.Drawing.Size(84, 28)
$refreshButton.Text = "Refresh"
$form.Controls.Add($refreshButton)

$warningLabel = New-Object System.Windows.Forms.Label
$warningLabel.Location = New-Object System.Drawing.Point(18, 148)
$warningLabel.Size = New-Object System.Drawing.Size(664, 42)
$warningLabel.ForeColor = [System.Drawing.Color]::DarkRed
$warningLabel.Text = "All data on the selected drive will be destroyed. Type ERASE exactly to enable writing. Internal and system disks are never listed."
$form.Controls.Add($warningLabel)

$confirmBox = New-Object System.Windows.Forms.TextBox
$confirmBox.Location = New-Object System.Drawing.Point(18, 194)
$confirmBox.Size = New-Object System.Drawing.Size(150, 24)
$form.Controls.Add($confirmBox)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(18, 238)
$progressBar.Size = New-Object System.Drawing.Size(664, 24)
$progressBar.Minimum = 0
$progressBar.Maximum = 1000
$form.Controls.Add($progressBar)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Location = New-Object System.Drawing.Point(18, 275)
$statusLabel.Size = New-Object System.Drawing.Size(664, 50)
$statusLabel.Text = "Select an image and removable drive."
$form.Controls.Add($statusLabel)

$writeButton = New-Object System.Windows.Forms.Button
$writeButton.Location = New-Object System.Drawing.Point(532, 338)
$writeButton.Size = New-Object System.Drawing.Size(150, 34)
$writeButton.Text = "Write SD card"
$form.Controls.Add($writeButton)

$openDialog = New-Object System.Windows.Forms.OpenFileDialog
$openDialog.Title = "Select raw SD image"
$openDialog.Filter = "Raw disk images (*.wic;*.img)|*.wic;*.img|All files (*.*)|*.*"
$openDialog.CheckFileExists = $true

$scriptDirectory = Split-Path -Parent $PSCommandPath
$repositoryRoot = Split-Path -Parent $scriptDirectory
$repositoryImage = Join-Path $repositoryRoot "software\petalinux\cora-z7-10-baseline\images\linux\petalinux-sdimage.wic"
$nearbyImage = Join-Path $scriptDirectory "petalinux-sdimage.wic"
if ($ImagePath) {
    $imageBox.Text = $ImagePath
}
elseif (Test-Path -LiteralPath $repositoryImage -PathType Leaf) {
    $imageBox.Text = $repositoryImage
}
elseif (Test-Path -LiteralPath $nearbyImage -PathType Leaf) {
    $imageBox.Text = $nearbyImage
}

function Refresh-DriveList {
    try {
        $script:diskMap.Clear()
        $driveBox.Items.Clear()
        foreach ($disk in @(Get-SafeRemovableDisks)) {
            $script:diskMap[$disk.Label] = $disk
            [void]$driveBox.Items.Add($disk.Label)
        }
        if ($driveBox.Items.Count -eq 1) { $driveBox.SelectedIndex = 0 }
        $confirmBox.Text = ""
        if ($driveBox.Items.Count -eq 0) {
            $statusLabel.Text = "No non-system USB, SD, or MMC drive was found. Insert the card and click Refresh."
        }
        else {
            $statusLabel.Text = "Found $($driveBox.Items.Count) eligible removable drive(s)."
        }
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show(
            "Drive discovery failed.`r`n`r`n$($_.Exception.Message)",
            "Cora SD Image Writer",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
}

$browseButton.Add_Click({
    if ($openDialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $imageBox.Text = $openDialog.FileName
    }
})

$refreshButton.Add_Click({ Refresh-DriveList })
$driveBox.Add_SelectedIndexChanged({ $confirmBox.Text = "" })

$writeButton.Add_Click({
    try {
        if ($script:writerActive) { return }
        if (-not (Test-Path -LiteralPath $imageBox.Text -PathType Leaf)) {
            throw "Select an existing .wic or .img image file."
        }
        $image = Get-Item -LiteralPath $imageBox.Text
        if ($image.Length -le 0) { throw "The selected image is empty." }
        if ($driveBox.SelectedIndex -lt 0) { throw "Select a removable target drive." }
        if ($confirmBox.Text -cne "ERASE") { throw "Type ERASE exactly before writing." }

        $selected = $script:diskMap[[string]$driveBox.SelectedItem]
        $current = @(Get-SafeRemovableDisks | Where-Object { $_.Number -eq $selected.Number })
        if ($current.Count -ne 1 -or $current[0].Size -ne $selected.Size) {
            throw "The selected drive changed. Click Refresh and select it again."
        }
        $selected = $current[0]
        $imageDrive = [System.IO.Path]::GetPathRoot($image.FullName).TrimEnd('\').TrimEnd(':')
        if (@($selected.Letters) -contains $imageDrive) {
            throw "The image file cannot be stored on the SD card that will be erased. Copy it to the Windows computer first."
        }
        if ([long]$image.Length -gt [long]$selected.Size) {
            throw "The image ($(Format-ByteSize $image.Length)) is larger than the selected drive ($(Format-ByteSize $selected.Size))."
        }

        $message = "Permanently erase:`r`n`r`n$($selected.Label)`r`n`r`nand write:`r`n`r`n$($image.FullName)`r`n`r`nContinue?"
        $answer = [System.Windows.Forms.MessageBox]::Show(
            $message,
            "Final erase confirmation",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Warning,
            [System.Windows.Forms.MessageBoxDefaultButton]::Button2
        )
        if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) { return }

        $script:writerActive = $true
        $writeButton.Enabled = $false
        $refreshButton.Enabled = $false
        $browseButton.Enabled = $false
        $driveBox.Enabled = $false
        $imageBox.Enabled = $false
        $confirmBox.Enabled = $false
        $progressBar.Value = 0
        $statusLabel.Text = "Starting raw write..."
        $script:writeTask = [CoraRawDiskWriter]::Start(
            $image.FullName,
            [int]$selected.Number,
            [long]$selected.Size,
            [string[]]$selected.Letters,
            $script:updates
        )
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message,
            "Cannot start write",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$timer.Add_Tick({
    $update = $null
    while ($script:updates.TryDequeue([ref]$update)) {
        $statusLabel.Text = $update.Message
        if ($update.Kind -eq "Progress") {
            $value = [Math]::Max(0, [Math]::Min(1000, [int]($update.Percent * 10)))
            $progressBar.Value = $value
        }
        elseif ($update.Kind -eq "Done") {
            $progressBar.Value = 1000
            $script:writerActive = $false
            $writeButton.Enabled = $true
            $refreshButton.Enabled = $true
            $browseButton.Enabled = $true
            $driveBox.Enabled = $true
            $imageBox.Enabled = $true
            $confirmBox.Enabled = $true
            $confirmBox.Text = ""
            [System.Windows.Forms.MessageBox]::Show(
                "$($update.Message)`r`n`r`nIf Windows asks to format a Linux partition, choose Cancel.",
                "Write complete",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            ) | Out-Null
        }
        elseif ($update.Kind -eq "Error") {
            $script:writerActive = $false
            $writeButton.Enabled = $true
            $refreshButton.Enabled = $true
            $browseButton.Enabled = $true
            $driveBox.Enabled = $true
            $imageBox.Enabled = $true
            $confirmBox.Enabled = $true
            $confirmBox.Text = ""
            $statusLabel.Text = "The write did not complete."
            [System.Windows.Forms.MessageBox]::Show(
                $update.Message,
                "Write failed",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            ) | Out-Null
        }
        $update = $null
    }
})

$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:writerActive) {
        $eventArgs.Cancel = $true
        [System.Windows.Forms.MessageBox]::Show(
            "The writer cannot be closed while an SD-card write is in progress.",
            "Write in progress",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
    }
})

Refresh-DriveList
$timer.Start()
[void]$form.ShowDialog()
$timer.Stop()
