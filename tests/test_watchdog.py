"""専用ウィンドウを閉じたらサーバも終わる、の見張り。

**時間で競走させない。** 実時間で「N 秒待って落ちるか」を見る形は負荷の高い CI で
落ちる（過去にリリースを止めた）。`sleep` を差し替えて、**手で時計を進める**。
"""

from __future__ import annotations

import threading

import pytest

from glosspop import appwindow, watchdog


@pytest.fixture(autouse=True)
def clean():
    watchdog.reset()
    yield
    watchdog.reset()


def test_it_does_nothing_until_armed():
    """`serve` では数えない。ページを閉じただけで落ちては困る。"""
    assert watchdog.armed() is False
    watchdog.touch()
    assert watchdog.idle_seconds() == 0.0


def test_arming_starts_counting():
    watchdog.arm()
    assert watchdog.armed() is True
    assert watchdog.idle_seconds() >= 0.0


class FakeClock:
    """`watch` に渡す時計。**見張りは 1 周ごとにここで止まる。**

    ただ足すだけの `sleep` にすると、見張りのスレッドが好きなだけ空回りして
    時計を何百秒も進めてしまい、テストが「知らせているのに終わった」と言い出す
    （実際にそうなった）。テストが `tick()` を呼ぶまで進ませないこと。
    """

    def __init__(self, monkeypatch):
        self.now = 1000.0
        self._go = threading.Semaphore(0)
        self._at_sleep = threading.Semaphore(0)
        monkeypatch.setattr(watchdog.time, "monotonic", lambda: self.now)

    def sleep(self, seconds):
        self._at_sleep.release()            # 「1 周終わってここに居ます」
        self._go.acquire()
        self.now += seconds

    def tick(self, timeout=2.0) -> bool:
        """見張りを 1 周させる。もう止まっている（結論を出した）なら偽。"""
        if not self._at_sleep.acquire(timeout=timeout):
            return False
        self._go.release()
        return True


def test_it_stops_when_the_pings_stop(monkeypatch):
    """合図が途絶えたら終わらせる（＝窓を閉じた）。"""
    clock = FakeClock(monkeypatch)
    fired = threading.Event()
    watchdog.arm()
    watchdog.watch(fired.set, sleep=clock.sleep)
    watchdog.touch()                        # 1 回は見えている
    for _ in range(60):
        if fired.wait(0.01):
            break
        clock.tick()
    assert fired.is_set(), "途絶えても終わらなかった"


def test_it_keeps_running_while_pings_arrive(monkeypatch):
    """合図が続いている間は終わらせない。"""
    clock = FakeClock(monkeypatch)
    fired = threading.Event()
    watchdog.arm()
    watchdog.watch(fired.set, sleep=clock.sleep)
    for _ in range(60):
        watchdog.touch()                    # 読み続けている
        clock.tick()
        assert not fired.is_set(), "知らせているのに終わってしまった"


def test_it_gives_the_window_time_to_open():
    """**開くまでの猶予は見切りより長い。** ブラウザの起動に数秒かかる。

    猶予と見切りが同じだと、窓が出る前に落ちる。
    """
    assert watchdog.GRACE_SECONDS > watchdog.IDLE_SECONDS


def test_it_gives_up_if_no_page_ever_arrives(monkeypatch):
    """窓が開けなかったときは、誰も見ていないサーバを残さない。

    ただし**見切りより長く待つ**（猶予のぶん）。ここが同じだと上のテストと
    区別が付かないので、猶予のあいだは終わらないことも見る。
    """
    clock = FakeClock(monkeypatch)
    fired = threading.Event()
    watchdog.arm()                          # 合図は一度も来ない
    watchdog.watch(fired.set, sleep=clock.sleep)
    ticks = int(watchdog.IDLE_SECONDS / watchdog.CHECK_SECONDS) + 1
    for _ in range(ticks):
        clock.tick()
    assert not fired.is_set(), "窓が開く前に諦めた（猶予が効いていない）"
    for _ in range(200):
        if fired.wait(0.01):
            break
        clock.tick()
    assert fired.is_set(), "誰も見ていないのに残り続けた"


def test_the_page_pings_more_often_than_the_server_gives_up():
    """ページ側の間隔がサーバの見切りより長いと、読んでいる最中に落ちる。

    値は `heartbeat.js` の `PING_MS` にあるので、そちらを直したらここも直すこと。
    """
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "glosspop" / "static" / "heartbeat.js"
    text = js.read_text(encoding="utf-8")
    ping_ms = int(text.split("const PING_MS =")[1].split(";")[0])
    assert ping_ms / 1000 * 2 <= watchdog.IDLE_SECONDS, (
        "ページの間隔が長すぎる。1 回落としただけでサーバが終わる"
    )


class TestOpeningTheWindowWaitsForOurOwnServer:
    """窓を出す合図は「**自分の**サーバが listen したか」。

    ポートが開いたかで見ると、閉じた直後に開き直したとき（前のサーバが生存確認で
    終わるまで 20 秒ほどある）に**もうすぐ死ぬ古いサーバへ向いた窓**が開く。
    """

    class FakeServer:
        def __init__(self, starts_after=0):
            self.started = False
            self._left = starts_after

    def test_it_waits_until_started(self):
        from glosspop import cli

        server = self.FakeServer()
        ticks = []

        def sleep(_):
            ticks.append(1)
            if len(ticks) == 3:
                server.started = True

        assert cli._wait_started(server, sleep=sleep) is True
        assert len(ticks) == 3

    def test_it_gives_up_if_we_never_bind(self):
        """ポートが使われていて bind に失敗したら、窓は開かない。"""
        from glosspop import cli

        assert cli._wait_started(self.FakeServer(), timeout=0.2, sleep=lambda _: None) is False


def test_the_console_hack_is_gone():
    """**`FreeConsole` の細工を戻さないこと。**

    以前は exe を 1 本 (`console=True`) にして、窓が開いた時点でコンソールから
    離脱していた。**親にコンソールが無くなると子が自分の窓を作る**ので、AI の
    下書きのたびに黒い窓が開き、それを閉じると下書きごと落ちた
    （`[WinError 6] ハンドルが無効です` も同じ原因）。

    いまは `glosspopw.exe` を `console=False` で作って正攻法にしてある。
    戻すなら、まず子プロセス側の窓（`CREATE_NO_WINDOW`）から考えること。
    """
    assert not hasattr(appwindow, "hide_own_console")
    assert not hasattr(appwindow, "console_is_ours")
