Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W3 {
  [DllImport("user32.dll")] public static extern IntPtr GetTopWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint c);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
$GW_HWNDNEXT = 2
$h = [W3]::GetTopWindow([IntPtr]::Zero)
$n = 0
while ($h -ne [IntPtr]::Zero -and $n -lt 25) {
  $sb = New-Object System.Text.StringBuilder(256)
  [W3]::GetWindowText($h, $sb, 256) | Out-Null
  $fg = ""
  if ($h -eq [W3]::GetForegroundWindow()) { $fg = " [FG]" }
  echo "$n : $($sb.ToString())$fg"
  $h = [W3]::GetWindow($h, $GW_HWNDNEXT)
  $n++
}
