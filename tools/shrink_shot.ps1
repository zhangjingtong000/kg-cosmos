# 把过大的截图缩到可读尺寸（harness 对读图有像素上限）
# 用法: pwsh -File tools/shrink_shot.ps1 -In a.png -Out b.jpg -Width 1440 [-Crop "x,y,w,h"]
param(
  [Parameter(Mandatory=$true)][string]$In,
  [Parameter(Mandatory=$true)][string]$Out,
  [int]$Width = 1440,
  [string]$Crop = ""
)
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($In)
if ($Crop -ne "") {
  $p = $Crop.Split(',')
  $rect = New-Object System.Drawing.Rectangle -ArgumentList ([int]$p[0]), ([int]$p[1]), ([int]$p[2]), ([int]$p[3])
  $bmp0 = New-Object System.Drawing.Bitmap -ArgumentList $rect.Width, $rect.Height
  $g0 = [System.Drawing.Graphics]::FromImage($bmp0)
  $dst = New-Object System.Drawing.Rectangle -ArgumentList 0, 0, $rect.Width, $rect.Height
  $g0.DrawImage($src, $dst, $rect, [System.Drawing.GraphicsUnit]::Pixel)
  $g0.Dispose(); $src.Dispose()
  $src = $bmp0
}
$w = $Width; $h = [int]($src.Height * $w / $src.Width)
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.DrawImage($src, 0, 0, $w, $h)
$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$ps = New-Object System.Drawing.Imaging.EncoderParameters(1)
$ps.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, 84)
$bmp.Save($Out, $enc, $ps)
$g.Dispose(); $bmp.Dispose(); $src.Dispose()
Write-Host "saved $Out  ($w x $h)"
