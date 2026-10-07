Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
}
"@
$p = Get-Process -Name 'SnowRunner' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p) {
  [W]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
  [W]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  echo "focused: $($p.MainWindowTitle)"
} else { echo "no game process" }
Start-Sleep -Seconds 3
Add-Type -AssemblyName System.Windows.Forms
$b = New-Object System.Drawing.Bitmap(1280, 720)
$g = [System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen(0, 0, 0, 0, $b.Size)
$b.Save('C:\Users\RANDOMAIZZER\AppData\Local\Temp\opencode\sr_screen.png')
$g.Dispose(); $b.Dispose()
echo shot-ok
