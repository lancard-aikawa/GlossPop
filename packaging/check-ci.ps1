<#
.SYNOPSIS
  リリース前の予行演習。CI と同じ条件で落ちないことを手元で確かめる。

.DESCRIPTION
  タグを打ってから CI で落ちると、**壊れたタグを消して付け直す往復**が要るうえ、
  GitHub から失敗のメールが飛ぶ。実際に 2 回続けて踏んだので、その 2 件が
  再現する条件をここに固めてある。どちらも**手元では通って CI で落ちる**種類だった。

    1. `claude` が PATH にあるかの差
       手元にはあるので `ai.available()` が真になり、AI 経路のエンドポイントは
       503 を返さず先まで進む。CI には無いので 503 で止まる。
       -> PATH から外し、GLOSSPOP_CLAUDE_BIN も空にして走らせる

    2. 実時間に依存するテスト
       別スレッドで遅れて listen させて繋がるのを待つ、といった形は負荷の高い
       マシンで落ちる。20 回に 1 回落ちるものがリリースを止めた。
       -> 時間に関わるテストだけ繰り返し回して、たまたま通っただけでないか見る

  さらに release ワークフローと同じ順で、バージョンの一致・ビルド・exe 起動まで
  通す。**ネットワークは conftest が常に塞いでいる**ので、ここでは何もしない。

.PARAMETER Port
  exe の確認に使うポート。既定 8765。

.PARAMETER Repeat
  時間に依存するテストを何回まわすか。既定 40。

  **これは網であって証明ではない。** 20 回に 1 回落ちるものを 20 回まわしても
  捕まえられるのは 6 割程度（実測で 5 回中 3 回）。40 回で 9 割弱。確実にしたい
  なら回数ではなく、テストの側を決定的に書き直すこと。

.PARAMETER SkipBuild
  ビルドと exe の確認を飛ばす（テストだけ手早く見たいとき）。
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [int]$Repeat = 40,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Write-Step($text) {
    Write-Host ''
    Write-Host "== $text" -ForegroundColor Cyan
}

function Fail($text) {
    Write-Host "NG: $text" -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------- #
# 1. バージョンの一致（ワークフローの最初の関門と同じ）
# --------------------------------------------------------------------------- #
Write-Step 'バージョンの一致'
$init = (Select-String -Path glosspop/__init__.py -Pattern '^__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$proj = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "  __init__.py : $init"
Write-Host "  pyproject   : $proj"
if ($init -ne $proj) {
    Fail "pyproject.toml ($proj) と glosspop/__init__.py ($init) が食い違っています"
}
$tag = (& git tag --list "v$init")
if ($tag) {
    Write-Host "  注意: タグ v$init はすでにあります（上げ忘れ？）" -ForegroundColor Yellow
}

# --------------------------------------------------------------------------- #
# 1b. その版の変更点が書かれているか
#
#     Release の本文は CHANGELOG.md のこの節から作る。**無いとワークフローが
#     落ちる**ので、タグを打つ前にここで気付く（バージョンの一致と同じ扱い）。
# --------------------------------------------------------------------------- #
Write-Step '変更点（CHANGELOG.md）'
$lines = Get-Content CHANGELOG.md
$at = ($lines | Select-String -Pattern "^##\s+$([regex]::Escape($init))\s*$").LineNumber
if (-not $at) {
    Fail "CHANGELOG.md に $init の節がありません。Release の本文になるので書き足すこと"
}
$rest = $lines[$at..($lines.Count - 1)]
$end = ($rest | Select-String -Pattern '^##\s' | Select-Object -First 1).LineNumber
$body = (($(if ($end) { $rest[0..($end - 2)] } else { $rest })) -join "`n").Trim()
if (-not $body) { Fail "CHANGELOG.md の $init の節が空です" }
Write-Host "  Release にはこれが出ます:"
$body -split "`n" | ForEach-Object { Write-Host "    $_" }

# --------------------------------------------------------------------------- #
# 2. claude 抜きで全テスト
# --------------------------------------------------------------------------- #
Write-Step "claude 抜きで全テスト（CI には claude が無い）"
# **uv の場所を先に押さえる。** claude と同じフォルダに入っていることがあり
# （どちらも ~/.local/bin）、PATH から外すと uv まで消える（実際に踏んだ）
$uv = (Get-Command uv -ErrorAction Stop).Source
$claude = (Get-Command claude -ErrorAction SilentlyContinue)
$savedPath = $env:PATH
try {
    if ($claude) {
        $dir = Split-Path -Parent $claude.Source
        Write-Host "  PATH から外す: $dir"
        $env:PATH = ($env:PATH -split ';' | Where-Object { $_ -and $_ -ne $dir }) -join ';'
        if (Get-Command claude -ErrorAction SilentlyContinue) {
            Fail 'claude を PATH から外しきれませんでした'
        }
    } else {
        Write-Host '  claude はもともと PATH にありません'
    }
    & $uv run pytest -q
    if ($LASTEXITCODE -ne 0) { Fail 'テストが落ちました（claude 抜き）' }
}
finally {
    $env:PATH = $savedPath
}

# --------------------------------------------------------------------------- #
# 3. 時間に依存するテストの掃き出し
# --------------------------------------------------------------------------- #
Write-Step "時間に依存するテストを $Repeat 回（たまたま通っただけでないか）"
# 実時間・スレッド・サブプロセスを使うもの。増えたらここに足す
$timing = @(
    'tests/test_appwindow.py',
    'tests/test_picker.py'
)
$present = $timing | Where-Object { Test-Path $_ }
Write-Host ("  対象: " + ($present -join ', '))
for ($i = 1; $i -le $Repeat; $i++) {
    & $uv run pytest @present -q --no-header -p no:cacheprovider | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "$i 回目で落ちました。時間で競走しているテストがあります（回数を数える形に書き直すこと）"
    }
}
Write-Host "  $Repeat 回とも通りました（網であって証明ではない）"

if ($SkipBuild) {
    Write-Host ''
    Write-Host 'OK: テストは CI と同じ条件で通ります（ビルドは飛ばしました）' -ForegroundColor Green
    exit 0
}

# --------------------------------------------------------------------------- #
# 4. 配布と同じビルド + exe の確認（ワークフローと同じ）
# --------------------------------------------------------------------------- #
Write-Step '配布用ビルド（-Seed dist）'
& (Join-Path $PSScriptRoot 'build.ps1') -Seed dist
if ($LASTEXITCODE -ne 0) { Fail 'ビルドが落ちました' }

Write-Step 'exe を起動して確認'
& (Join-Path $PSScriptRoot 'check-exe.ps1') -Port $Port
if ($LASTEXITCODE -ne 0) { Fail 'exe の確認が落ちました' }

# --------------------------------------------------------------------------- #
# 5. 宿題の見直し（docs/open-questions.md が置き去りになっていないか）
#
#    前に、機能が 5 つ入る間この file は一度も更新されず、**解決済みの項目が
#    残ったまま**になった。残っていると「まだ無いもの」に見えるので害がある。
#    タグを打つ直前は必ずここを通るので、**最後に読み上げて目に入れる**。
#
#    落とさず警告にしてあるのは、「触った」が「見直した」の証明にならないため
#    （バージョンの上げ忘れと同じ扱い）。
# --------------------------------------------------------------------------- #
Write-Step '宿題の見直し（docs/open-questions.md）'
$notes = 'docs/open-questions.md'
$lastTag = (& git describe --tags --abbrev=0 2>$null)
if ($LASTEXITCODE -eq 0 -and $lastTag) {
    $since = @(& git log --format=%h "$lastTag..HEAD")
    $touched = @(& git log --format=%h "$lastTag..HEAD" -- $notes)
    Write-Host "  $lastTag 以降: コミット $($since.Count) 件 / うち $notes を触ったもの $($touched.Count) 件"
    if ($since.Count -gt 0 -and $touched.Count -eq 0) {
        Write-Host "  注意: $lastTag 以降この file を一度も見直していません（片付いた項目が残っていないか）" -ForegroundColor Yellow
    }
} else {
    Write-Host '  前回のタグが無いので、コミットとの比較は飛ばします'
}
Write-Host '  いま残っている宿題:'
Select-String -Path $notes -Pattern '^## ' | ForEach-Object {
    Write-Host ('    ' + ($_.Line -replace '^##\s*', ''))
}

Write-Host ''
Write-Host "OK: $init はこのままタグを打てます" -ForegroundColor Green
Write-Host '  git tag v' -NoNewline; Write-Host $init
Write-Host "  git push origin v$init"
exit 0
