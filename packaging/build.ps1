<#
.SYNOPSIS
  GlossPop を Windows 向けに onedir ビルドする。

.DESCRIPTION
  dist/GlossPop/ に glosspop.exe と _internal/ を作り、その隣へ
  content/ と data/ を配置する (辞書は exe の隣に読み書きされる)。
  そのフォルダごと配布・移動できる。

.PARAMETER Seed
  exe の隣に何を置くか。

    dev  (既定) リポジトリの content/ と data/ をそのまま入れる。手元での確認用
    dist        content/ と packaging/sample-data/ を入れる。配布用 (個人の辞書は入れない)
    none        何も入れない (初回起動時に空で作られる)

.PARAMETER Fast
  PyInstaller のキャッシュを消さない (--clean を付けない)。手元で繰り返し確かめる用。
  実測で 48 秒 -> 21 秒。**spec や依存を変えたときは付けないこと** (古い解析結果が残る)。
#>
[CmdletBinding()]
param(
    [ValidateSet('dev', 'dist', 'none')]
    [string]$Seed = 'dev',
    [switch]$Fast
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

$pyi = @('packaging/glosspop.spec', '--noconfirm', '--distpath', 'dist', '--workpath', 'build')
if (-not $Fast) { $pyi += '--clean' }
uv run pyinstaller @pyi
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$out = Join-Path $root 'dist/GlossPop'

# 置くもの: 表示元 (content) と辞書 (data) の組み合わせをモードで決める
$seeds = switch ($Seed) {
    'dev'  { @{ content = 'content'; data = 'data' } }
    'dist' { @{ content = 'content'; data = 'packaging/sample-data' } }
    'none' { @{} }
}
foreach ($name in $seeds.Keys) {
    $src = Join-Path $root $seeds[$name]
    $dst = Join-Path $out $name
    if ((Test-Path $src) -and (-not (Test-Path $dst))) {
        Copy-Item $src $dst -Recurse
        Write-Host "seeded: $name <- $($seeds[$name])"
    }
}

Write-Host ""
Write-Host "ビルド完了: $out"
Write-Host "  起動: .\dist\GlossPop\glosspop.exe          (= app / 専用ウィンドウが開く)"
Write-Host "  辞書: .\dist\GlossPop\data\glossary\"
