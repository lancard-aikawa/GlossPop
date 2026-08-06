"""専用ウィンドウを閉じたらサーバも終わる、を成り立たせる生存確認。

**窓の寿命はプロセスでは追えない。** ``--app=<URL>`` で起動した ``msedge.exe`` は
ブラウザ本体を別プロセスで生んですぐ終了するので、こちらが掴んでいる ``Popen`` を
``wait()`` しても「窓が閉じた」ことにはならない（一度これで「窓は開いたままサーバ
だけ落ちる」を作った）。**追えるのはページの側からの合図だけ**なので、開いている
ページに定期的に知らせてもらい、途絶えたら終わる。

守ること 4 つ:

- **専用ウィンドウで開いたときだけ動く**（``arm()`` を呼ぶのは ``app`` の経路のみ）。
  ``serve`` は「ブラウザは自分で開く」ものなので、ページを閉じただけで落ちては困る
- **「閉じた」の合図は作らない。** ``pagehide`` で知らせる形にすると、
  **ページを移動しただけ**（辞書ページを直接開くなど）でサーバが落ちる。
  合図が**来なくなったこと**で判断すれば、移動の途中で落ちることはない
- **待つ時間は読み込みより十分長く**（``IDLE_SECONDS``）。ページの移動・再読み込み・
  重い文書の描画をまたいでも途切れたと見なさないため
- **開くまでの猶予を別に持つ**（``GRACE_SECONDS``）。ブラウザの起動には数秒かかる
  ので、立ち上げ直後を「誰も見ていない」と判定しない
"""

from __future__ import annotations

import threading
import time

#: 最後の合図からこれだけ途絶えたら終わる。ページ側は ``PING_SECONDS`` ごとに
#: 知らせるので、数回落ちても耐える長さにしてある
IDLE_SECONDS = 25.0

#: 立ち上げてから最初の合図を待つ時間。ブラウザの起動と最初の描画のぶん
GRACE_SECONDS = 90.0

#: 見張る間隔
CHECK_SECONDS = 2.0

_armed = False
_last_seen = 0.0
#: 合図が来た回数。**「一度でも見えたか」は時刻の比較では決めない** ——
#: 立ち上げと最初の合図が同じ瞬間になりうるので、回数で数えるほうが嘘をつかない
_pings = 0
_lock = threading.Lock()


def arm() -> None:
    """見張りを有効にする。**専用ウィンドウで開いたときだけ呼ぶこと。**"""
    global _armed, _last_seen
    with _lock:
        _armed = True
        _last_seen = time.monotonic()


def armed() -> bool:
    with _lock:
        return _armed


def touch() -> None:
    """ページからの合図。有効でないときは何もしない（数えるだけ無駄）。"""
    global _last_seen, _pings
    with _lock:
        if _armed:
            _last_seen = time.monotonic()
            _pings += 1


def pings() -> int:
    with _lock:
        return _pings


def idle_seconds() -> float:
    """最後の合図からの秒数。有効でなければ 0。"""
    with _lock:
        return 0.0 if not _armed else time.monotonic() - _last_seen


def reset() -> None:
    """テスト用。プロセス内に状態が残るので、fixture から戻す。"""
    global _armed, _last_seen, _pings
    with _lock:
        _armed = False
        _last_seen = 0.0
        _pings = 0


def watch(on_idle, *, idle: float = IDLE_SECONDS, grace: float = GRACE_SECONDS,
          interval: float = CHECK_SECONDS, sleep=time.sleep) -> threading.Thread:
    """途絶えたら ``on_idle()`` を呼ぶ見張りを別スレッドで走らせる。

    ``grace`` の間に 1 度も合図が来なければ、そこで諦めて終わる —— **窓が開けな
    かったときに、誰も見ていないサーバが残り続けるのを防ぐ**（ブラウザが無い環境で
    既定のブラウザに落ちたが、それも開けなかった、という筋道がありうる）。
    """
    at_start = pings()

    def run() -> None:
        seen_any = False
        while True:
            sleep(interval)
            if not armed():
                continue
            if pings() != at_start:
                seen_any = True          # 一度でも見えたら、以後は見切りのほう
            if idle_seconds() > (idle if seen_any else grace):
                on_idle()
                return

    thread = threading.Thread(target=run, daemon=True, name="glosspop-watchdog")
    thread.start()
    return thread
