Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W4 {
  [DllImport("user32.dll")] public static extern IntPtr GetTopWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint c);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
$GW_HWNDNEXT = 2
$h = [W4]::GetTopWindow([IntPtr]::Zero)
$n = 0
while ($h -ne [IntPtr]::Zero -and $n -lt 40) {
  $sb = New-Object System.Text.StringBuilder(256)
  $cl = New-Object System.Text.StringBuilder(256)
  [W4]::GetWindowText($h, $sb, 256) | Out-Null
  [W4]::GetClassName($h, $cl, 256) | Out-Null
  $pid = 0
  [W4]::GetWindowThreadProcessId($h, [ref]$pid) | Out-Null
  $vis = [W4]::IsWindowVisible($h)
  $fg = ""
  if ($h -eq [W4]::GetForegroundWindow()) { $fg = " [FG]" }
  try { $pn = (Get-Process -Id $pid -ErrorAction Stop).Name } catch { $pn = "?" }
  echo "$n pid=$pid($pn) vis=$vis cls=$($cl.ToString()) title=$($sb.ToString())$fg"
  $h = [W4]::GetWindow($h, $GW_HWNDNEXT)
  $n++
}
