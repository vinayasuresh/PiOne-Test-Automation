param([string]$FilePath)
$shell = New-Object -ComObject Shell.Application
$folderPath = [System.IO.Path]::GetDirectoryName((Resolve-Path $FilePath).Path)
$fileName = [System.IO.Path]::GetFileName((Resolve-Path $FilePath).Path)
$folder = $shell.Namespace($folderPath)
$file = $folder.ParseName($fileName)

Write-Host "Name:       " ($folder.GetDetailsOf($file, 0))
Write-Host "Size:       " ($folder.GetDetailsOf($file, 1))
Write-Host "Type:       " ($folder.GetDetailsOf($file, 2))
Write-Host "Duration:   " ($folder.GetDetailsOf($file, 27))
Write-Host "Dimensions: " ($folder.GetDetailsOf($file, 316))
Write-Host "Frame Rate: " ($folder.GetDetailsOf($file, 317))
Write-Host "Data Rate:  " ($folder.GetDetailsOf($file, 318))
