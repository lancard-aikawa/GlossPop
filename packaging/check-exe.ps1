<#
.SYNOPSIS
  ビルドした exe を起動して、凍結後にしか壊れないところを確かめる。

.DESCRIPTION
  `serve` で起動する (窓を開かせない)。見るのは release ワークフローと同じ 4 点:

    * `/api/health` が答える            … 文字列 import・DATA_ROOT がおかしいと死ぬ
    * 辞書が読めている (entry_count)    … data/ の置き場所を間違えると 0 になる
    * `/static/base.js` が配信される    … spec の datas 漏れ
    * `/api/render` が通る              … 依存の hiddenimports 漏れ

  あわせて **窓が開いていないこと** も見る。release ワークフローは `serve` を
  明示して起動するので、ここで窓が開くようになると CI が固まる。

.PARAMETER Port
  使うポート。既定 8765。

.PARAMETER Exe
  確かめる exe。既定 dist\GlossPop\glosspop.exe。
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Exe = 'dist\GlossPop\glosspop.exe'
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path $Exe)) {
    Write-Host "not built yet: $Exe"
    Write-Host '  run "check.cmd build" first'
    exit 1
}

# 動いているサーバが居るとポートを取れない。親ではなく所有者を落とす
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# アプリモードで開いている窓 (この URL のもの) のブラウザ本体だけを数える。
# --type= が付いているのは renderer / gpu などの子なので除く。
# **起動の前後で比べる。** 前から開いている窓を数えると、手元で試したものが
# 残っているだけで落ちる (実際そうなった)
function Get-AppWindowPids {
    @(
        Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -like "*--app=http://127.0.0.1:$Port/*" -and
                $_.CommandLine -notlike '*--type=*'
            }
    ).ProcessId
}
$before = Get-AppWindowPids

$log = Join-Path ([System.IO.Path]::GetTempPath()) 'glosspop-check'
$proc = Start-Process $Exe -ArgumentList 'serve', '--port', $Port -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$log-out.txt" -RedirectStandardError "$log-err.txt"

try {
    $health = $null
    foreach ($i in 1..40) {
        try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $health) {
        Get-Content "$log-err.txt" -Encoding utf8 -ErrorAction SilentlyContinue | Write-Host
        throw "exe が応答しませんでした (port $Port)"
    }

    Write-Host ("  version     : {0}" -f $health.version)
    Write-Host ("  entry_count : {0}" -f $health.entry_count)
    Write-Host ("  glossary    : {0}" -f $health.glossary_dir)
    if ($health.entry_count -lt 1) {
        throw "辞書が読めていません (entry_count = $($health.entry_count))"
    }

    $static = Invoke-WebRequest "http://127.0.0.1:$Port/static/base.js" -TimeoutSec 5
    if ($static.StatusCode -ne 200) { throw '静的ファイルが配信されていません' }
    Write-Host '  static      : ok'

    $body = @{ text = 'テスト'; kind = 'markdown' } | ConvertTo-Json
    Invoke-RestMethod "http://127.0.0.1:$Port/api/render" -Method Post `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json' | Out-Null
    Write-Host '  render      : ok'

    # serve で窓が開いてはいけない (開くと CI の起動確認が返ってこなくなる)
    $opened = @(Get-AppWindowPids | Where-Object { $_ -notin $before })
    Write-Host ("  new windows : {0}  (serve なので 0 が正しい)" -f $opened.Count)
    if ($opened.Count -ne 0) { throw 'serve なのに専用ウィンドウが開きました' }

    Write-Host '[exe] ok'
}
finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
}
