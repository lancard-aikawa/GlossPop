<#
.SYNOPSIS
  GlossPop を Windows 向けに onedir ビルドする。

.DESCRIPTION
  dist/GlossPop/ に glosspop.exe (CLI) / glosspopw.exe (窓なし) と _internal/ を作り、その隣へ
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

# 動いているサーバが dist や .venv のファイルを掴んでいるとビルドが失敗する。
#
# **止めるのは「このリポジトリのもの」だけ。** ポートを見ただけで断ると、
# 別の場所に入れた GlossPop（配布版を普段使いしている、など）が動いているだけで
# ビルドできなくなる —— あちらはこのリポジトリの dist も .venv も掴んでいないので、
# 止める理由が無い（実際にこれで詰まった）。
$busy = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
foreach ($conn in @($busy)) {
    $path = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).Path
    if ($path -and $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "ポート 8765 でこのリポジトリの GlossPop が動いています。先に止めてください:"
        Write-Warning "  Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }"
        Write-Warning "  (掴んでいるもの: $path)"
        exit 1
    }
    if ($path) {
        Write-Host "  ポート 8765 は別の GlossPop が使っています（このビルドには影響しません）: $path"
    }
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

# 配布用では、フォルダに置かれた辞書 (.glosspop) を持ち出さない。
# content/ は丸ごとコピーするので、手元で作ったフォルダ辞書がそのまま zip に入る。
# CI はクリーンチェックアウト (.glosspop は gitignore) なので出ないが、
# **手元でビルドしたものを配ると混入する**。実際に 22 語が入っていた
if ($Seed -eq 'dist') {
    Get-ChildItem $out -Recurse -Force -Directory -Filter '.glosspop' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "外した: $($_.FullName.Substring($out.Length + 1)) (フォルダ辞書は配布物に入れない)"
            Remove-Item $_.FullName -Recurse -Force
        }
}

Write-Host ""
Write-Host "ビルド完了: $out"
Write-Host "  起動: .\dist\GlossPop\glosspopw.exe         (= app / 専用ウィンドウが開く。窓なし)"
Write-Host "  CLI : .\dist\GlossPop\glosspop.exe list     (コンソール版)"
Write-Host "  辞書: .\dist\GlossPop\data\glossary\"
