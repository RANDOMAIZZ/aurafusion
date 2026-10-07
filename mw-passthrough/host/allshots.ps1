Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$i = 0
foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
  $b = New-Object System.Drawing.Bitmap($s.Bounds.Width, $s.Bounds.Height)
  $g = [System.Drawing.Graphics]::FromImage($b)
  $g.CopyFromScreen($s.Bounds.Location, [System.Drawing.Point]::Empty, $s.Bounds.Size)
  $b.Save("C:\Users\RANDOMAIZZER\AppData\Local\Temp\opencode\mon$i.png")
  $g.Dispose(); $b.Dispose()
  echo "mon$i $($s.Bounds)"
  $i++
}
