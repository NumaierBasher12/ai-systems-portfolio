# Windows' built-in OCR (signed OS component, no install). Prints one JSON line per jpg: {"f":"0001.jpg","t":"text"}
param([Parameter(Mandatory)][string]$Dir, [string]$Out)  # -Out: write results to a file (stdout is lost when the parent has no console, e.g. pythonw)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { Write-Error 'no OCR language installed'; exit 2 }
$lines = New-Object System.Collections.Generic.List[string]
foreach ($f in Get-ChildItem -LiteralPath $Dir -Filter *.jpg | Sort-Object Name) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $stream.Dispose()
        $lines.Add(([pscustomobject]@{ f = $f.Name; t = $res.Text } | ConvertTo-Json -Compress))
    } catch { $lines.Add(([pscustomobject]@{ f = $f.Name; t = '' } | ConvertTo-Json -Compress)) }
}
if ($Out) { [System.IO.File]::WriteAllLines($Out, $lines, (New-Object System.Text.UTF8Encoding($false))) } else { $lines }
