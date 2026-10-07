Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
"@
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$p = Get-Process -Name 'SnowRunner' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p) {
  echo "title: $($p.MainWindowTitle) iconic: $([W]::IsIconic($p.MainWindowHandle))"
  [W]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
  [W]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  Start-Sleep -Seconds 5
  echo "iconic now: $([W]::IsIconic($p.MainWindowHandle))"
  $i = 0
  foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $b = New-Object System.Drawing.Bitmap($s.Bounds.Width, $s.Bounds.Height)
    $g = [System.Drawing.Graphics]::FromImage($b)
    $g.CopyFromScreen($s.Bounds.Location, [System.Drawing.Point]::Empty, $s.Bounds.Size)
    $b.Save("C:\Users\RANDOMAIZZER\AppData\Local\Temp\opencode\g$i.png")
    $g.Dispose(); $b.Dispose()
    $i++
  }
  echo done
} else { echo "no game process" }
