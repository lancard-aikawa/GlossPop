"""新しい版の zip を落として、アプリの**隣に**展開する。

**自分自身は置き換えない。** Windows では動いている exe を差し替えられないし、
署名していないバイナリが自分を書き換える挙動はウイルス対策ソフトがいちばん嫌う。
`Program Files` に置かれていれば昇格も要る。隣に展開して「こちらを起動してください」
と言うだけなら、そのどれも起きず、**旧フォルダがそのまま戻り先**になる。

外から実行ファイルを取ってくる経路なので、守っていること:

- 落とすのは設定されたリポジトリの **release アセットだけ**（HTTPS）
- 拡張子は `.zip` のみ。サイズに上限
- GitHub が digest を返すときは **SHA-256 を検証**する
- **展開先の外に書かない**（zip slip）。zipfile 任せにせず自分で確かめる
- 既にあるフォルダには展開しない（上書きしない）
- 落としたものを実行しない
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

from . import __version__, config, updates

#: 受け取る zip の上限。配布物は 20 MB ほどなので、桁違いのものは弾く
MAX_BYTES = 256 * 1024 * 1024

#: 取得のタイムアウト（秒）。本体より長め
TIMEOUT = 300.0

#: 展開先の名前に使えない文字を落とす（タグはリポジトリ側の任意文字列）
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class InstallError(RuntimeError):
    pass


def target_dir(version: str) -> Path:
    """展開先。アプリと同じ階層に ``GlossPop-<version>`` を作る。

    タグはリポジトリ側の任意文字列なので、そのままフォルダ名にしない。
    区切り文字と連続するドットを潰したうえで、**組み立てた結果がアプリと同じ
    階層に収まっていること**を最後に確かめる。
    """
    name = _UNSAFE.sub("-", (version or "").strip().lstrip("v"))
    name = re.sub(r"\.{2,}", "-", name).strip(". ") or "new"
    dest = (config.APP_DIR.parent / f"GlossPop-{name[:60]}").resolve()
    if dest.parent != config.APP_DIR.parent.resolve():
        raise InstallError(f"展開先を組み立てられません: {version!r}")
    return dest


def pick_asset(assets: list[dict]) -> dict:
    """リリースの添付から Windows 版の zip を選ぶ。"""
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip") and "win" in name.lower():
            return asset
    for asset in assets:
        if str(asset.get("name") or "").lower().endswith(".zip"):
            return asset
    raise InstallError("リリースに zip が添付されていません")


def _check_digest(path: Path, declared: str) -> None:
    """GitHub が申告した digest と突き合わせる。申告が無ければ何もしない。"""
    if not declared:
        return
    algo, _, want = declared.partition(":")
    if algo.lower() != "sha256" or not want:
        return                      # 知らない形式で落とさない（検証しないだけ）
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest().lower() != want.strip().lower():
        raise InstallError("ダウンロードしたファイルが壊れています（ハッシュが一致しません）")


def safe_members(zf: zipfile.ZipFile, dest: Path) -> list[zipfile.ZipInfo]:
    """展開先の外に出る要素が無いことを確かめてから返す。

    ``zipfile`` も絶対パスと ``..`` は落とすが、**外から来た書庫をライブラリ任せに
    しない**。シンボリックリンクもここで弾く（Windows でも zip には入りうる）。
    """
    base = dest.resolve()
    members = []
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
            raise InstallError(f"展開先の外を指す要素があります: {name}")
        # 上位 4 ビットがファイル種別。0xA000 = シンボリックリンク
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            raise InstallError(f"シンボリックリンクは展開しません: {name}")
        resolved = (base / name).resolve()
        if resolved != base and base not in resolved.parents:
            raise InstallError(f"展開先の外に出ます: {name}")
        members.append(info)
    if not members:
        raise InstallError("zip が空です")
    return members


def _strip_root(members: list[zipfile.ZipInfo]) -> str:
    """配布 zip は ``GlossPop/`` を 1 枚かぶっている。あれば剥がす。

    剥がさないと ``GlossPop-0.5.0/GlossPop/glosspop.exe`` になって、
    「隣に展開したのに exe が見つからない」になる。
    """
    tops = {Path(m.filename).parts[0] for m in members if Path(m.filename).parts}
    return f"{tops.pop()}/" if len(tops) == 1 else ""


def extract(zip_path: Path, dest: Path) -> int:
    """zip を ``dest`` に展開してファイル数を返す。**既にあるフォルダには展開しない。**"""
    if dest.exists() and any(dest.iterdir()):
        raise InstallError(f"すでに中身のあるフォルダです: {dest}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InstallError("zip として読めませんでした") from exc

    with zf:
        members = safe_members(zf, dest)
        prefix = _strip_root(members)
        count = 0
        for info in members:
            rel = info.filename[len(prefix):] if prefix else info.filename
            if not rel:
                continue
            target = dest / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def _download(url: str, into: Path) -> int:
    """アセットを落とす。上限を超えたら途中で止める。"""
    size = 0
    with httpx.stream(
        "GET", url, timeout=TIMEOUT, follow_redirects=True,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"GlossPop/{__version__}",
        },
    ) as res:
        res.raise_for_status()
        with into.open("wb") as f:
            for chunk in res.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise InstallError("ファイルが大きすぎます")
                f.write(chunk)
    return size


def fetch_release() -> dict:
    """最新リリースの情報を取る（タグと添付）。"""
    url = f"https://api.github.com/repos/{updates.REPO}/releases/latest"
    try:
        res = httpx.get(
            url,
            timeout=updates.TIMEOUT,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"GlossPop/{__version__}",
            },
        )
        res.raise_for_status()
        return res.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InstallError(f"リリースの情報を取れませんでした: {exc}") from exc


def install_latest() -> dict:
    """最新版を落として隣に展開する。**起動はしない。**

    返すのは ``{version, dir, files, size, verified}``。
    """
    release = fetch_release()
    tag = str(release.get("tag_name") or "")
    if not updates.is_newer(tag, __version__):
        raise InstallError(f"すでに最新です（{__version__}）")

    asset = pick_asset(release.get("assets") or [])
    dest = target_dir(tag)
    if dest.exists() and any(dest.iterdir()):
        raise InstallError(f"すでに展開されています: {dest}")

    url = str(asset.get("browser_download_url") or "")
    if not url.startswith("https://"):
        raise InstallError("ダウンロード先が https ではありません")

    with tempfile.TemporaryDirectory(prefix="glosspop-update-") as tmp:
        zip_path = Path(tmp) / "release.zip"
        try:
            size = _download(url, zip_path)
        except httpx.HTTPError as exc:
            raise InstallError(f"ダウンロードに失敗しました: {exc}") from exc

        declared = str(asset.get("digest") or "")
        _check_digest(zip_path, declared)
        try:
            files = extract(zip_path, dest)
        except InstallError:
            # 途中まで書いたものを残さない（半端なフォルダを起動されると危ない）
            shutil.rmtree(dest, ignore_errors=True)
            raise

    return {
        "version": tag,
        "dir": str(dest),
        "files": files,
        "size": size,
        "verified": bool(declared),
    }
