# ui.ps1 -- minimal desktop automation harness: screenshot + SendInput.
# Usage:
#   ui.ps1 shot [outfile]                 full-screen PNG (default shot.png)
#   ui.ps1 click X Y | rclick X Y | dbl X Y
#   ui.ps1 drag X1 Y1 X2 Y2 [steps] [holdms]
#   ui.ps1 move X Y
#   ui.ps1 type "text"                    SendKeys literal-escaped text
#   ui.ps1 key "{ENTER}" | "^s" | "{F2}"  raw SendKeys syntax
#   ui.ps1 wheel DELTA [X Y]              scroll at point (120 per notch)
#   ui.ps1 res                            print WxH
param([string]$cmd = "res")

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class U {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public UIntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public UIntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct HARDWAREINPUT { public uint uMsg; public ushort wParamL, wParamH; }
    [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public HARDWAREINPUT hi;
    }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION U; }
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
    [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr hWnd, bool altTab);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hWnd, ref POINT point);
    public const uint LD = 0x02, LU = 0x04, RD = 0x08, RU = 0x10, WHEEL = 0x0800;
    public static void LClick(){ mouse_event(LD,0,0,0,UIntPtr.Zero); System.Threading.Thread.Sleep(40); mouse_event(LU,0,0,0,UIntPtr.Zero); }
    public static void RClick(){ mouse_event(RD,0,0,0,UIntPtr.Zero); System.Threading.Thread.Sleep(40); mouse_event(RU,0,0,0,UIntPtr.Zero); }
    public static void LDown(){ mouse_event(LD,0,0,0,UIntPtr.Zero); }
    public static void LUp(){ mouse_event(LU,0,0,0,UIntPtr.Zero); }
    public static void ScrollWheel(int d){ mouse_event(WHEEL,0,0,d,UIntPtr.Zero); }
    public static void Key(byte key) {
        keybd_event(key, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(55);
        keybd_event(key, 0, 2, UIntPtr.Zero);
    }
    public static void Chord(byte modifier, byte key) {
        keybd_event(modifier, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(55);
        Key(key);
        System.Threading.Thread.Sleep(55);
        keybd_event(modifier, 0, 2, UIntPtr.Zero);
    }
    public static void SendVk(ushort key) {
        var inputs = new INPUT[2];
        inputs[0].type = 1; inputs[0].U.ki.wVk = key;
        inputs[1].type = 1; inputs[1].U.ki.wVk = key; inputs[1].U.ki.dwFlags = 2;
        if (SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT))) != 2) throw new System.ComponentModel.Win32Exception();
        System.Threading.Thread.Sleep(55);
    }
    public static void SendChord(ushort modifier, ushort key) {
        var inputs = new INPUT[4];
        inputs[0].type = 1; inputs[0].U.ki.wVk = modifier;
        inputs[1].type = 1; inputs[1].U.ki.wVk = key;
        inputs[2].type = 1; inputs[2].U.ki.wVk = key; inputs[2].U.ki.dwFlags = 2;
        inputs[3].type = 1; inputs[3].U.ki.wVk = modifier; inputs[3].U.ki.dwFlags = 2;
        if (SendInput(4, inputs, Marshal.SizeOf(typeof(INPUT))) != 4) throw new System.ComponentModel.Win32Exception();
        System.Threading.Thread.Sleep(55);
    }
    public static void CtrlClick() {
        keybd_event(0x11, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(55);
        LClick();
        System.Threading.Thread.Sleep(55);
        keybd_event(0x11, 0, 2, UIntPtr.Zero);
    }
    public static IntPtr FindWindowContaining(string wanted) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
            if (!IsWindowVisible(hWnd)) return true;
            var title = new StringBuilder(512);
            GetWindowText(hWnd, title, title.Capacity);
            if (title.ToString().IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0) {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
    public static bool FocusWindow(string wanted) {
        var hWnd = FindWindowContaining(wanted);
        if (hWnd == IntPtr.Zero) return false;
        ShowWindow(hWnd, 9);
        var foreground = GetForegroundWindow();
        uint currentThread = GetCurrentThreadId();
        uint targetThread = GetWindowThreadProcessId(hWnd, IntPtr.Zero);
        uint foregroundThread = foreground == IntPtr.Zero ? 0 : GetWindowThreadProcessId(foreground, IntPtr.Zero);
        if (foregroundThread != 0 && foregroundThread != currentThread) AttachThreadInput(currentThread, foregroundThread, true);
        if (targetThread != 0 && targetThread != currentThread) AttachThreadInput(currentThread, targetThread, true);
        try {
            BringWindowToTop(hWnd);
            SwitchToThisWindow(hWnd, true);
            SetForegroundWindow(hWnd);
            SetActiveWindow(hWnd);
            SetFocus(hWnd);
        } finally {
            if (targetThread != 0 && targetThread != currentThread) AttachThreadInput(currentThread, targetThread, false);
            if (foregroundThread != 0 && foregroundThread != currentThread) AttachThreadInput(currentThread, foregroundThread, false);
        }
        System.Threading.Thread.Sleep(180);
        return GetForegroundWindow() == hWnd;
    }
    public static bool PositionWindow(string wanted, int x, int y, int width, int height) {
        var hWnd = FindWindowContaining(wanted);
        if (hWnd == IntPtr.Zero) return false;
        ShowWindow(hWnd, 9);
        System.Threading.Thread.Sleep(120);
        return MoveWindow(hWnd, x, y, width, height, true);
    }
    public static int[] GetClientBox(string wanted) {
        var hWnd = FindWindowContaining(wanted);
        if (hWnd == IntPtr.Zero) return null;
        RECT rect;
        if (!GetClientRect(hWnd, out rect)) return null;
        var point = new POINT();
        if (!ClientToScreen(hWnd, ref point)) return null;
        return new int[] { point.X, point.Y, rect.Right - rect.Left, rect.Bottom - rect.Top };
    }
    public static bool LooksLikeSingleZero(System.Drawing.Bitmap bmp) {
        int minX = bmp.Width, minY = bmp.Height, maxX = -1, maxY = -1;
        for (int y = 0; y < bmp.Height; y++) for (int x = 0; x < bmp.Width; x++) {
            var c = bmp.GetPixel(x, y);
            if (c.R > 205 && c.G > 205 && c.B > 205) {
                if (x < minX) minX = x; if (x > maxX) maxX = x;
                if (y < minY) minY = y; if (y > maxY) maxY = y;
            }
        }
        if (maxX < 0) return false;
        int width = maxX - minX + 1, height = maxY - minY + 1;
        if (height < 24 || width < 17 || width > 42 || width * 100 / height < 45) return false;
        int midY = (minY + maxY) / 2;
        int left = 0, right = 0, centre = 0;
        for (int y = midY - 2; y <= midY + 2; y++) for (int x = minX; x <= maxX; x++) {
            var c = bmp.GetPixel(x, y);
            if (!(c.R > 205 && c.G > 205 && c.B > 205)) continue;
            int rel = (x - minX) * 100 / Math.Max(1, width - 1);
            if (rel < 28) left++; else if (rel > 72) right++; else centre++;
        }
        return left >= 3 && right >= 3 && centre <= 2;
    }
}
"@

# RealDash may be hosted on a monitor whose scale differs from the system DPI.
# Per-monitor-v2 keeps SetCursorPos and CopyFromScreen in the same physical-pixel
# coordinate space.  Fall back for older Windows builds.
if (-not [U]::SetProcessDpiAwarenessContext([IntPtr](-4))) {
  [void][U]::SetProcessDPIAware()
}
$a = $args

function MoveTo($x, $y) { [void][U]::SetCursorPos([int]$x, [int]$y); Start-Sleep -Milliseconds 60 }
function FocusRealDash() {
  if ([U]::FocusWindow("RealDash")) { return }
  $process = Get-Process -Name "RealDash","RealDash_uwp" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
  if ($null -eq $process) { throw "RealDash window not found" }
  [void][U]::BringWindowToTop($process.MainWindowHandle)
  [void][U]::SetForegroundWindow($process.MainWindowHandle)
  Start-Sleep -Milliseconds 180
}
function SaveShot($out) {
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
  $g.Dispose(); $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}
function SaveScreenShot($screenIndex, $out) {
  $screens = [System.Windows.Forms.Screen]::AllScreens
  if ($screenIndex -lt 0 -or $screenIndex -ge $screens.Count) {
    throw "screen index $screenIndex outside 0..$($screens.Count - 1)"
  }
  $b = $screens[$screenIndex].Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
  $g.Dispose(); $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}
function SaveScreenShotWithCursor($screenIndex, $out) {
  $screens = [System.Windows.Forms.Screen]::AllScreens
  if ($screenIndex -lt 0 -or $screenIndex -ge $screens.Count) {
    throw "screen index $screenIndex outside 0..$($screens.Count - 1)"
  }
  $b = $screens[$screenIndex].Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
  $point = New-Object U+POINT
  if ([U]::GetCursorPos([ref]$point)) {
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::Magenta), 5
    $cx = $point.X - $b.X; $cy = $point.Y - $b.Y
    $g.DrawLine($pen, $cx - 18, $cy, $cx + 18, $cy)
    $g.DrawLine($pen, $cx, $cy - 18, $cx, $cy + 18)
    $pen.Dispose()
  }
  $g.Dispose(); $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}
function SaveClientShot($out) {
  $box = [U]::GetClientBox("RealDash")
  if ($null -eq $box) { throw "RealDash client area not found" }
  $bmp = New-Object System.Drawing.Bitmap $box[2], $box[3]
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($box[0], $box[1], 0, 0, $bmp.Size)
  $g.Dispose(); $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}

switch ($cmd) {
  "res" {
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
    "res $($b.Width)x$($b.Height) at $($b.X),$($b.Y)"
  }
  "shot" {
    $out = if ($a.Count -ge 1) { $a[0] } else { Join-Path $PSScriptRoot "shot.png" }
    SaveShot $out
    "shot -> $out"
  }
  "shotscreen" {
    SaveScreenShot ([int]$a[0]) $a[1]
    "screen $($a[0]) shot -> $($a[1])"
  }
  "shotscreencursor" {
    SaveScreenShotWithCursor ([int]$a[0]) $a[1]
    "screen $($a[0]) cursor shot -> $($a[1])"
  }
  "shotclient" {
    SaveClientShot $a[0]
    "RealDash client shot -> $($a[0])"
  }
  "focus"  { FocusRealDash; "focused RealDash" }
  "position" {
    if (-not [U]::PositionWindow("RealDash", [int]$a[0], [int]$a[1], [int]$a[2], [int]$a[3])) { throw "RealDash window not found or could not be positioned" }
    Start-Sleep -Milliseconds 250
    "positioned RealDash $($a[0]),$($a[1]) $($a[2])x$($a[3])"
  }
  "click"  { FocusRealDash; MoveTo $a[0] $a[1]; [U]::LClick(); "click $($a[0]),$($a[1])" }
  "clickshot" {
    FocusRealDash; MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 120
    SaveShot $a[2]; "click $($a[0]),$($a[1]); shot -> $($a[2])"
  }
  "ctrlclickshot" {
    FocusRealDash; MoveTo $a[0] $a[1]; [U]::CtrlClick(); Start-Sleep -Milliseconds 120
    SaveShot $a[2]; "ctrl-click $($a[0]),$($a[1]); shot -> $($a[2])"
  }
  "filterselectshot" {
    FocusRealDash
    MoveTo 520 290; [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 200}'); Start-Sleep -Milliseconds 80
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 180
    MoveTo 500 350
    if ($a[1] -eq 'ctrl') { [U]::CtrlClick() } else { [U]::LClick() }
    Start-Sleep -Milliseconds 150
    SaveShot $a[2]
    "filter/select '$($a[0])' mode=$($a[1]); shot -> $($a[2])"
  }
  "filtershot" {
    FocusRealDash
    MoveTo 520 290; [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 200}'); Start-Sleep -Milliseconds 80
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 180
    SaveShot $a[1]
    "filter '$($a[0])'; shot -> $($a[1])"
  }
  "filterrowsmove" {
    FocusRealDash
    $query = $a[0]
    $rows = $a[1].Split(',') | ForEach-Object { [int]$_ }
    $direction = $a[2].ToLowerInvariant()
    $pageCount = [int]$a[3]
    $prefix = $a[4]
    MoveTo 520 290; [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 200}'); Start-Sleep -Milliseconds 80
    $t = $query -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 180
    SaveShot "$prefix-00-filter.png"
    for ($i = 0; $i -lt $rows.Count; $i++) {
      MoveTo 500 $rows[$i]
      if ($i -eq 0) { [U]::LClick() } else { [U]::CtrlClick() }
      Start-Sleep -Milliseconds 120
      SaveShot ("{0}-{1:D2}-select.png" -f $prefix, ($i + 1))
    }
    [U]::Chord(0x11, 0x58); Start-Sleep -Milliseconds 220
    SaveShot "$prefix-20-cut.png"
    $pageX = if ($direction -eq 'next') { 1343 } elseif ($direction -eq 'prev') { 780 } else { throw "direction must be next or prev" }
    for ($i = 0; $i -lt $pageCount; $i++) {
      MoveTo $pageX 926; [U]::LClick(); Start-Sleep -Milliseconds 180
      SaveShot ("{0}-{1:D2}-page.png" -f $prefix, (21 + $i))
    }
    [U]::Chord(0x11, 0x56); Start-Sleep -Milliseconds 260
    SaveShot "$prefix-30-paste.png"
    "moved rows $($a[1]) for '$query' $direction $pageCount page(s); screenshots -> $prefix-*"
  }
  "hotkeyshot" {
    FocusRealDash
    $modifier = switch ($a[0].ToUpperInvariant()) {
      "SHIFT" { 0x10 }
      "CTRL"  { 0x11 }
      "ALT"   { 0x12 }
      default { throw "unknown modifier $($a[0])" }
    }
    $key = [Convert]::ToByte($a[1], 16)
    [U]::Chord([byte]$modifier, $key); Start-Sleep -Milliseconds 180
    SaveShot $a[2]
    "hotkey $($a[0])+$($a[1]); shot -> $($a[2])"
  }
  "menuclickshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 160
    MoveTo $a[2] $a[3]; [U]::LClick(); Start-Sleep -Milliseconds 180
    SaveShot $a[4]
    "menu click $($a[0]),$($a[1]) -> $($a[2]),$($a[3]); shot -> $($a[4])"
  }
  "filesaveshot" {
    FocusRealDash
    MoveTo 474 190; [U]::LClick(); Start-Sleep -Milliseconds 180
    SaveShot "$($a[0])-menu.png"
    MoveTo 542 451; [U]::LClick(); Start-Sleep -Milliseconds 260
    SaveShot "$($a[0])-saved.png"
    "explicit File > Save; screenshots -> $($a[0])-*"
  }
  "clickraw" { MoveTo $a[0] $a[1]; [U]::LClick(); "click raw $($a[0]),$($a[1])" }
  "moveraw" { MoveTo $a[0] $a[1]; "move raw $($a[0]),$($a[1])" }
  "clickreplacetyperawshot" {
    # Generic one-transaction helper for short-lived native dialogs.  The
    # click establishes focus, Ctrl+A replaces the existing token, Enter
    # commits, and the screenshot records the resulting state.
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 80
    [U]::Chord(0x11, 0x41); Start-Sleep -Milliseconds 60
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 80
    [U]::Key(0x0D); Start-Sleep -Milliseconds 500
    SaveScreenShot ([int]$a[3]) $a[4]
    "click/replace/type/Enter raw at $($a[0]),$($a[1]); screen $($a[3]) shot -> $($a[4])"
  }
  "clickrawshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 180
    SaveShot $a[2]; "click raw $($a[0]),$($a[1]); shot -> $($a[2])"
  }
  "clickrawscreenshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 180
    SaveScreenShot ([int]$a[2]) $a[3]
    "click raw $($a[0]),$($a[1]); screen $($a[2]) shot -> $($a[3])"
  }
  "dblrawscreenshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 90; [U]::LClick()
    Start-Sleep -Milliseconds 300
    SaveScreenShot ([int]$a[2]) $a[3]
    "double-click raw $($a[0]),$($a[1]); screen $($a[2]) shot -> $($a[3])"
  }
  "rawmenuclickscreenshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 220
    MoveTo $a[2] $a[3]; [U]::LClick(); Start-Sleep -Milliseconds 400
    SaveScreenShot ([int]$a[4]) $a[5]
    "raw menu click $($a[0]),$($a[1]) -> $($a[2]),$($a[3]); screen $($a[4]) shot -> $($a[5])"
  }
  "rawfieldenterscreenshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 100
    [U]::Chord(0x11, 0x41); Start-Sleep -Milliseconds 80
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 900
    SaveScreenShot ([int]$a[3]) $a[4]
    "raw field $($a[0]),$($a[1]) entered; screen $($a[3]) shot -> $($a[4])"
  }
  "focusfieldclearenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 120
    # RealDash numeric boxes do not reliably honor Ctrl+A.  Its editor notes
    # document END followed by explicit backspaces as the dependable clear.
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 24}'); Start-Sleep -Milliseconds 80
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 700
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; cleared field $($a[0]),$($a[1]) and entered value; screen $($a[3]) shot -> $($a[4])"
  }
  "focusfieldselectenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 120
    [System.Windows.Forms.SendKeys]::SendWait('{HOME}+{END}'); Start-Sleep -Milliseconds 100
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 700
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; selected entire field $($a[0]),$($a[1]) and entered value; screen $($a[3]) shot -> $($a[4])"
  }
  "focusdbltypeenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 90; [U]::LClick()
    Start-Sleep -Milliseconds 160
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 700
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; double-click-selected field $($a[0]),$($a[1]) and entered value; screen $($a[3]) shot -> $($a[4])"
  }
  "focustripltypeenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]
    [U]::LClick(); Start-Sleep -Milliseconds 65; [U]::LClick(); Start-Sleep -Milliseconds 65; [U]::LClick()
    Start-Sleep -Milliseconds 160
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 700
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; triple-click-selected field $($a[0]),$($a[1]) and entered value; screen $($a[3]) shot -> $($a[4])"
  }
  "focusdragselecttypeenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LDown(); Start-Sleep -Milliseconds 100
    for ($i = 1; $i -le 12; $i++) {
      $x = [int]$a[0] + ([int]$a[2] - [int]$a[0]) * $i / 12
      $y = [int]$a[1] + ([int]$a[3] - [int]$a[1]) * $i / 12
      [void][U]::SetCursorPos($x, $y); Start-Sleep -Milliseconds 18
    }
    [U]::LUp(); Start-Sleep -Milliseconds 120
    $t = $a[4] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 700
    SaveScreenShot ([int]$a[5]) $a[6]
    "focused RealDash; drag-selected $($a[0]),$($a[1]) -> $($a[2]),$($a[3]) and entered value; screen $($a[5]) shot -> $($a[6])"
  }
  "dblfieldenterscreenshot" {
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 80
    [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('^a'); Start-Sleep -Milliseconds 80
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 500
    SaveScreenShot ([int]$a[3]) $a[4]
    "double-click raw field $($a[0]),$($a[1]) entered; screen $($a[3]) shot -> $($a[4])"
  }
  "focusdblfieldenterscreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 80
    [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('^a'); Start-Sleep -Milliseconds 80
    $t = $a[2] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Milliseconds 500
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; double-click raw field $($a[0]),$($a[1]) entered; screen $($a[3]) shot -> $($a[4])"
  }
  "focusnumericreplacescreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 80
    [U]::LClick(); Start-Sleep -Milliseconds 100
    [U]::Chord(0x11, 0x41); Start-Sleep -Milliseconds 80
    foreach ($ch in $a[2].ToCharArray()) {
      if ($ch -ge '0' -and $ch -le '9') {
        [U]::Key([byte](0x30 + ([int]$ch - [int][char]'0')))
      } elseif ($ch -eq '-') {
        [U]::Key(0xBD)
      } elseif ($ch -eq '.') {
        [U]::Key(0xBE)
      } else {
        throw "unsupported numeric character '$ch'"
      }
    }
    [U]::Key(0x0D); Start-Sleep -Milliseconds 500
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; low-level numeric replace at $($a[0]),$($a[1]); screen $($a[3]) shot -> $($a[4])"
  }
  "focussendinputnumericreplacescreenshot" {
    FocusRealDash
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 80
    [U]::LClick(); Start-Sleep -Milliseconds 100
    [U]::SendChord(0x11, 0x41); Start-Sleep -Milliseconds 80
    foreach ($ch in $a[2].ToCharArray()) {
      if ($ch -ge '0' -and $ch -le '9') {
        [U]::SendVk([uint16](0x30 + ([int]$ch - [int][char]'0')))
      } elseif ($ch -eq '-') {
        [U]::SendVk(0xBD)
      } elseif ($ch -eq '.') {
        [U]::SendVk(0xBE)
      } else {
        throw "unsupported numeric character '$ch'"
      }
    }
    [U]::SendVk(0x0D); Start-Sleep -Milliseconds 500
    SaveScreenShot ([int]$a[3]) $a[4]
    "focused RealDash; SendInput numeric replace at $($a[0]),$($a[1]); screen $($a[3]) shot -> $($a[4])"
  }
  "filterselectrawshot" {
    # Full-screen 1920x1080 editor coordinates.  Do not call FocusRealDash:
    # the UWP process has no usable MainWindowHandle while full-screen.
    MoveTo 219 252; [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 200}'); Start-Sleep -Milliseconds 80
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 220
    # The first filtered result is centred at y=345 in the 1920x1080 editor.
    MoveTo 210 345; [U]::LClick(); Start-Sleep -Milliseconds 180
    SaveShot $a[1]
    "full-screen filter/select '$($a[0])'; shot -> $($a[1])"
  }
  "filterselectcoordscreenshot" {
    FocusRealDash
    MoveTo $a[1] $a[2]; [U]::LClick(); Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait('{END}{BACKSPACE 200}'); Start-Sleep -Milliseconds 80
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); Start-Sleep -Milliseconds 260
    MoveTo $a[3] $a[4]; [U]::LClick(); Start-Sleep -Milliseconds 220
    SaveScreenShot ([int]$a[5]) $a[6]
    "filter/select '$($a[0])' at $($a[1]),$($a[2]) -> $($a[3]),$($a[4]); screen $($a[5]) shot -> $($a[6])"
  }
  "rclick" { FocusRealDash; MoveTo $a[0] $a[1]; [U]::RClick(); "rclick $($a[0]),$($a[1])" }
  "dbl"    { FocusRealDash; MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 90; [U]::LClick(); "dbl $($a[0]),$($a[1])" }
  "move"   { FocusRealDash; MoveTo $a[0] $a[1]; "move $($a[0]),$($a[1])" }
  "drag"   {
    FocusRealDash
    $steps = if ($a.Count -ge 5) { [int]$a[4] } else { 20 }
    $hold  = if ($a.Count -ge 6) { [int]$a[5] } else { 120 }
    MoveTo $a[0] $a[1]; [U]::LDown(); Start-Sleep -Milliseconds $hold
    for ($i = 1; $i -le $steps; $i++) {
      $x = [int]$a[0] + ([int]$a[2] - [int]$a[0]) * $i / $steps
      $y = [int]$a[1] + ([int]$a[3] - [int]$a[1]) * $i / $steps
      [void][U]::SetCursorPos([int]$x, [int]$y); Start-Sleep -Milliseconds 14
    }
    Start-Sleep -Milliseconds $hold; [U]::LUp()
    "drag $($a[0]),$($a[1]) -> $($a[2]),$($a[3])"
  }
  "dragraw"   {
    $steps = if ($a.Count -ge 5) { [int]$a[4] } else { 20 }
    $hold  = if ($a.Count -ge 6) { [int]$a[5] } else { 120 }
    MoveTo $a[0] $a[1]; [U]::LDown(); Start-Sleep -Milliseconds $hold
    for ($i = 1; $i -le $steps; $i++) {
      $x = [int]$a[0] + ([int]$a[2] - [int]$a[0]) * $i / $steps
      $y = [int]$a[1] + ([int]$a[3] - [int]$a[1]) * $i / $steps
      [void][U]::SetCursorPos([int]$x, [int]$y); Start-Sleep -Milliseconds 14
    }
    Start-Sleep -Milliseconds $hold; [U]::LUp()
    "drag raw $($a[0]),$($a[1]) -> $($a[2]),$($a[3])"
  }
  "type" {
    FocusRealDash
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); "typed"
  }
  "typeraw" {
    $t = $a[0] -replace '([+^%~(){}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($t); "typed raw"
  }
  "key"  { FocusRealDash; [System.Windows.Forms.SendKeys]::SendWait($a[0]); "sent $($a[0])" }
  "keyraw"  { [System.Windows.Forms.SendKeys]::SendWait($a[0]); "sent raw $($a[0])" }
  "keyrawshot"  {
    [System.Windows.Forms.SendKeys]::SendWait($a[0]); Start-Sleep -Milliseconds 180
    SaveShot $a[1]; "sent raw $($a[0]); shot -> $($a[1])"
  }
  "keyrawscreenshot"  {
    [System.Windows.Forms.SendKeys]::SendWait($a[0]); Start-Sleep -Milliseconds 180
    SaveScreenShot ([int]$a[1]) $a[2]
    "sent raw $($a[0]); screen $($a[1]) shot -> $($a[2])"
  }
  "vkey" {
    FocusRealDash
    $key = [Convert]::ToByte($a[0], 16)
    [U]::Key($key); "vkey $($a[0])"
  }
  "spaceeditshot" {
    # The run-mode menu fades after roughly five seconds.  Keep Space, the
    # settled-menu delay, the Edit click, and the proof screenshot in one
    # foreground transaction so Codex cannot reclaim focus between steps.
    FocusRealDash
    [U]::Key(0x20); Start-Sleep -Milliseconds 900
    MoveTo $a[0] $a[1]; [U]::LClick(); Start-Sleep -Milliseconds 1000
    SaveScreenShot ([int]$a[2]) $a[3]
    "Space -> Edit $($a[0]),$($a[1]); screen $($a[2]) shot -> $($a[3])"
  }
  "hotkey" {
    FocusRealDash
    $modifier = switch ($a[0].ToUpperInvariant()) {
      "SHIFT" { 0x10 }
      "CTRL"  { 0x11 }
      "ALT"   { 0x12 }
      default { throw "unknown modifier $($a[0])" }
    }
    $key = [Convert]::ToByte($a[1], 16)
    [U]::Chord([byte]$modifier, $key); "hotkey $($a[0])+$($a[1])"
  }
  "hotkeyraw" {
    $modifier = switch ($a[0].ToUpperInvariant()) {
      "SHIFT" { 0x10 }
      "CTRL"  { 0x11 }
      "ALT"   { 0x12 }
      default { throw "unknown modifier $($a[0])" }
    }
    $key = [Convert]::ToByte($a[1], 16)
    [U]::Chord([byte]$modifier, $key); "hotkey raw $($a[0])+$($a[1])"
  }
  "waitzeroedit" {
    FocusRealDash
    $box = [U]::GetClientBox("RealDash")
    if ($null -eq $box) { throw "RealDash client area not found" }
    $scale = [Math]::Min($box[2] / 1920.0, $box[3] / 1080.0)
    $offsetX = ($box[2] - 1920.0 * $scale) / 2.0
    $offsetY = ($box[3] - 1080.0 * $scale) / 2.0
    # Existing page-1 speed value: Text Gauge 48 at 885,762 218x72.
    $sx = [int][Math]::Round($box[0] + $offsetX + 885 * $scale)
    $sy = [int][Math]::Round($box[1] + $offsetY + 762 * $scale)
    $sw = [int][Math]::Round(218 * $scale)
    $sh = [int][Math]::Round(72 * $scale)
    $bmp = New-Object System.Drawing.Bitmap $sw, $sh
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    $matched = $false
    while ([DateTime]::UtcNow -lt $deadline) {
      $g.CopyFromScreen($sx, $sy, 0, 0, $bmp.Size)
      if ([U]::LooksLikeSingleZero($bmp)) { $matched = $true; break }
      Start-Sleep -Milliseconds 45
    }
    $g.Dispose(); $bmp.Dispose()
    if (-not $matched) { throw "timed out waiting for displayed vehicle speed 0" }
    [U]::Chord(0x10, 0x36)
    "speed zero observed; sent Shift+6"
  }
  "wheel" {
    FocusRealDash
    if ($a.Count -ge 3) { MoveTo $a[1] $a[2] }
    [U]::ScrollWheel([int]$a[0]); "wheel $($a[0])"
  }
  default { "unknown cmd: $cmd" }
}
