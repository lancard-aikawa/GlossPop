<#
.SYNOPSIS
  GlossPop を Windows 向けに onedir ビルドする。

.DESCRIPTION
  dist/GlossPop/ に glosspop.exe と _internal/ を作り、その隣へ
  content/ と data/ を配置する (辞書は exe の隣に読み書きされる)。
  そのフォルダごと配布・移動できる。

.PARAMETER NoSeed
  リポジトリの content/ data/ をコピーしない (空の状態で配る場合)。
#>
[CmdletBinding()]
param(
    [switch]$NoSeed
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 動いているサーバが dist や .venv のファイルを掴んでいるとビルドが失敗する
$busy = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Warning "ポート 8765 で GlossPop が動いています。先に止めてください:"
    Write-Warning "  Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }"
    exit 1
}

uv sync --group build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pyinstaller packaging/glosspop.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$out = Join-Path $root 'dist/GlossPop'

if (-not $NoSeed) {
    foreach ($name in 'content', 'data') {
        $src = Join-Path $root $name
        $dst = Join-Path $out $name
        if ((Test-Path $src) -and (-not (Test-Path $dst))) {
            Copy-Item $src $dst -Recurse
            Write-Host "seeded: $name"
        }
    }
}

Write-Host ""
Write-Host "ビルド完了: $out"
Write-Host "  起動: .\dist\GlossPop\glosspop.exe          (= serve)"
Write-Host "  辞書: .\dist\GlossPop\data\glossary\"
