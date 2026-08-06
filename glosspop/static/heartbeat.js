// 専用ウィンドウで開いているとき、ページが生きていることをサーバに知らせる。
// 途絶えるとサーバが自分で終わる（→ glosspop/watchdog.py）。
//
// **全ページに要る。** ビューアだけに置くと、辞書ページを直接開いて読んでいる間に
// サーバが落ちる。settings.js と同じく **HTML に script タグを 1 行足すだけ**で、
// 各ページの JS からは import しない。
//
// **「閉じました」を送る形にしないこと。** `pagehide` で知らせると、ページを移動
// しただけ（覆いではなく本物の遷移）でサーバが落ちる。合図が**来なくなったこと**で
// 判断すれば、移動の途中で落ちることはない。

//: 知らせる間隔。サーバ側の見切り (watchdog.IDLE_SECONDS) の半分以下にして、
//: 1〜2 回落としても途切れたと見なされないようにする
const PING_MS = 8000;

let timer = null;

async function ping() {
  try {
    const res = await fetch("/api/alive", { method: "POST" });
    const data = await res.json();
    // **専用ウィンドウでないなら知らせるのをやめる。** ふだんのブラウザのタブから
    // 開いているときは数えていないので、送り続けても無駄なだけ
    if (!data.armed && timer) {
      clearInterval(timer);
      timer = null;
    }
  } catch {
    /* サーバが落ちていれば届かない。次の回で拾えるので何もしない */
  }
}

export function installHeartbeat() {
  if (timer) return;                       // 二重に仕掛けない
  ping();                                  // 開いた直後に 1 回（猶予を短くできる）
  timer = setInterval(ping, PING_MS);
  // 隠れていた窓が戻ってきたら、間隔を待たずに知らせる（画面を切り替えて
  // 戻ってきた直後に落ちる、を避ける）
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ping();
  });
}

installHeartbeat();
