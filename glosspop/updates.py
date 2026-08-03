"""新しい版が出ているかを GitHub Releases に聞く。

**このアプリが外へ通信する唯一の常時経路**なので、扱いを明示しておく:

- 既定で有効。GitHub Releases から落とすアプリなので、GitHub を見に行くのは
  予想の範囲と判断した。ただし ⚙ と ``GLOSSPOP_UPDATE_CHECK=0`` で切れる
- **lifespan では叩かない。** 起動のたびに勝手に出ていくのを避けるのと、
  テスト（TestClient は lifespan を走らせる）が外へ出ないようにするため。
  ``/api/update`` を叩かれたときだけ動く＝ブラウザで開いたときだけ
- 1 日に 1 回まで。時刻は設定ファイルに残すので、再起動を繰り返しても増えない
- **失敗しても黙る。** ネットが無い・GitHub が落ちている・レート制限に当たった、
  のどれでも本体の動作には関係が無い
"""

from __future__ import annotations

import os
import re
import time

import httpx

from . import __version__, config

#: 見に行くリポジトリ
REPO = os.environ.get("GLOSSPOP_UPDATE_REPO", "lancard-aikawa/GlossPop")

#: 確認の間隔（秒）。1 日 1 回で十分
CHECK_INTERVAL = int(os.environ.get("GLOSSPOP_UPDATE_INTERVAL", str(24 * 3600)))

#: 短くする。更新の確認で画面を待たせない
TIMEOUT = float(os.environ.get("GLOSSPOP_UPDATE_TIMEOUT", "5"))

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")

#: 設定ファイルに残す鍵
_LAST_CHECKED = "update_last_checked"
_LATEST = "update_latest"

#: プロセス内のキャッシュ。ページを開き直すたびに聞きに行かないため
_cache: dict | None = None


def enabled() -> bool:
    """更新の確認をするか。環境変数 > 設定ファイル > 既定 (する)。"""
    raw = os.environ.get("GLOSSPOP_UPDATE_CHECK")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    value = config.load_settings().get("update_check")
    return True if value is None else bool(value)


def parse_version(text: str) -> tuple[int, ...] | None:
    """``v0.4.0`` → ``(0, 4, 0)``。読めなければ ``None``。"""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """``latest`` が ``current`` より新しいか。

    **読めない版は「新しくない」に倒す。** 読めない文字列で「更新があります」と
    出すほうが害が大きい（ユーザーは確かめようがない）。
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    # 桁数が違っても比べられるように 0 で埋める (0.4 と 0.4.1)
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def _remember(latest: str) -> None:
    """確認した時刻と結果を設定ファイルに残す。

    書く直前に読み直すのは、⚙ からの保存と踏み合わないようにするため。
    """
    settings = config.load_settings()
    settings[_LAST_CHECKED] = int(time.time())
    settings[_LATEST] = latest
    try:
        config.save_settings(settings)
    except OSError:
        pass          # 残せなくても、そのプロセスの間はキャッシュが効く


def _result(latest: str, *, checked_at: int, error: str = "") -> dict:
    return {
        "enabled": True,
        "current": __version__,
        "latest": latest,
        "newer": is_newer(latest, __version__) if latest else False,
        "url": f"https://github.com/{REPO}/releases/latest",
        "checked_at": checked_at,
        "error": error,
    }


def _disabled() -> dict:
    return {
        "enabled": False,
        "current": __version__,
        "latest": "",
        "newer": False,
        "url": f"https://github.com/{REPO}/releases/latest",
        "checked_at": 0,
        "error": "",
    }


def invalidate() -> None:
    global _cache
    _cache = None


async def check(*, force: bool = False) -> dict:
    """新しい版があるか調べる。**失敗しても例外にしない。**

    直近の結果が ``CHECK_INTERVAL`` 以内なら聞きに行かない（``force`` で無視）。
    """
    global _cache
    if not enabled():
        return _disabled()
    if _cache is not None and not force:
        return _cache

    settings = config.load_settings()
    last = int(settings.get(_LAST_CHECKED) or 0)
    now = int(time.time())
    if not force and last and now - last < CHECK_INTERVAL:
        # 前回の結果を使う。再起動を繰り返しても GitHub を叩かない
        _cache = _result(str(settings.get(_LATEST) or ""), checked_at=last)
        return _cache

    latest = ""
    error = ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            res = await client.get(
                f"https://api.github.com/repos/{REPO}/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"GlossPop/{__version__}",
                },
            )
            res.raise_for_status()
            # ドラフトと事前公開は /releases/latest に出てこない (それでよい)
            latest = str(res.json().get("tag_name") or "")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        error = str(exc)

    if latest:
        _remember(latest)
        _cache = _result(latest, checked_at=now)
    else:
        # 失敗は覚えない。次に開いたときに素直に再試行させる
        _cache = _result(str(settings.get(_LATEST) or ""), checked_at=last, error=error)
    return _cache
