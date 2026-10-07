Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class W2 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
}
"@
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$p = Get-Process -Name 'SnowRunner' -ErrorAction SilentlyContinue | Select-Object -First 1
$h = $p.MainWindowHandle
echo "visible: $([W2]::IsWindowVisible($h)) style: $([W2]::GetWindowLong($h, -16)) exstyle: $([W2]::GetWindowLong($h, -20))"
[W2]::ShowWindow($h, 9) | Out-Null
$r1 = [W2]::SetForegroundWindow($h)
Start-Sleep -Seconds 2
$fg = [W2]::GetForegroundWindow()
echo "setfg=$r1 fg hine=$($fg -eq $h)"
$b = New-Object System.Drawing.Bitmap(1920, 1080)
$g = [System.Drawing.Graphics]::FromImage($b)
$pw = [W2]::PrintWindow($h, $g.GetHdc(), 0)
$g.ReleaseHdc()
echo "printwindow=$pw"
$g.Dispose()
$b.Save('C:\Users\RANDOMAIZZER\AppData\Local\Temp\opencode\pw.png')
echo saved
