// 本文の読み上げ (Web Speech API)。
//
// Edge の「リーディング モード」でも読み上げはできるが、あれは本文を抜き出して
// **別の描画に置き換える**ので、自動リンクも吹き出しも効かなくなる。ここで自前に
// 持つのは「聞きながら辞書が生きている」状態を作るため。
//
// 読み上げは**ブロック単位で 1 つずつ**投げ、`onend` で次に進める。長い文字列を
// 一度に渡すと Chromium が途中で切る (既知の癖) うえ、いま読んでいる場所が
// 分からなくなってハイライトを合わせられない。
//
// 音声はローカルのものだけ。Edge の「ナチュラル」音声は Web Speech API からは
// 使えないので、滑らかさではリーディング モードに劣る。

const VOICE_KEY = "glosspop.voice";
const RATE_KEY = "glosspop.rate";

/** 読み上げる要素。`pre` は中身がコードなので読まない (聞いても分からない)。 */
const BLOCKS = "p, li, h1, h2, h3, h4, h5, h6, blockquote, dd, dt, td, th, figcaption";
const SKIP_INSIDE = "pre";

/**
 * 1 回の発話に渡す最大文字数。
 *
 * Chromium には「1 つの発話が 15 秒ほどを超えると `speaking` が true のまま無音に
 * なり `onend` も来なくなる」という既知の癖がある。**この環境では再現しなかった**
 * （106 文字 = 20.2 秒でも最後まで鳴った）が、報告の多い不具合なので短く切って
 * 渡しておく。日本語はおよそ 6〜8 文字/秒なので、50 文字なら遅い速度でも 10 秒
 * ほどに収まる。日本語の 1 文はたいていこれ以下なので、普通の文章では文の途中で
 * 切れることはない。
 */
const MAX_CHUNK = 50;

/** 文の切れ目。句点のたぐいの**後ろ**で切る。 */
const SENTENCE_END = /(?<=[。．！？!?])/;

/** 発話に渡す単位へ切る。文で切り、それでも長ければ読点、無ければ長さで切る。 */
export function toChunks(text) {
  const out = [];
  for (const sentence of (text || "").split(SENTENCE_END)) {
    let rest = sentence.trim();
    while (rest.length > MAX_CHUNK) {
      const head = rest.slice(0, MAX_CHUNK);
      const at = Math.max(head.lastIndexOf("、"), head.lastIndexOf("，"), head.lastIndexOf(" "));
      // 切れ目が頭のほうにしか無いなら、いっそ長さで切る (細切れにしない)
      const cut = at > MAX_CHUNK / 2 ? at + 1 : MAX_CHUNK;
      out.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trim();
    }
    if (rest) out.push(rest);
  }
  return out;
}

/** 読み上げ対象のブロックを本文の順に集める。 */
function collectBlocks(root) {
  return [...root.querySelectorAll(BLOCKS)].filter((node) => {
    if (node.closest(SKIP_INSIDE)) return false;
    // 入れ子のブロック (li の中の p など) は内側だけを採る。両方読むと二重になる
    if (node.querySelector(BLOCKS)) return false;
    return node.textContent.trim().length > 0;
  });
}

export function available() {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** 日本語を先に、ローカル音声だけを返す。voices は非同期で埋まる。 */
function loadVoices() {
  return new Promise((resolve) => {
    const pick = () => {
      const all = speechSynthesis.getVoices().filter((v) => v.localService !== false);
      if (!all.length) return null;
      const ja = all.filter((v) => v.lang.toLowerCase().startsWith("ja"));
      return [...ja, ...all.filter((v) => !ja.includes(v))];
    };
    const first = pick();
    if (first) return resolve(first);
    // 初回はまだ空。voiceschanged を待つ (来ないブラウザもあるので時間で諦める)
    const done = () => {
      speechSynthesis.removeEventListener("voiceschanged", done);
      clearTimeout(timer);
      resolve(pick() || []);
    };
    const timer = setTimeout(done, 1500);
    speechSynthesis.addEventListener("voiceschanged", done);
  });
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 保存できなくてもその回の選択は効く */
  }
}

function recall(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

/**
 * 読み上げ機を作る。
 *
 * @param {object} o
 * @param {HTMLElement} o.root      本文の要素 (この中のブロックを読む)
 * @param {function} [o.onState]    状態が変わったときに呼ばれる ({playing, paused, index, total})
 */
export function createReader({ root, onState = () => {} }) {
  let blocks = [];
  let index = -1;
  /** いま読んでいる段落を切ったもの (`MAX_CHUNK` 参照) と、その何番目か。 */
  let chunks = [];
  let chunkAt = 0;
  let playing = false;
  /**
   * 一時停止しているか。**`speechSynthesis.paused` を直接見ない。**
   * `pause()` / `resume()` は非同期で、呼んだ直後に読むと前の値が返るので、
   * ボタンの表示が 1 手遅れる（実際にそうなった）。
   */
  let paused = false;
  /**
   * 発話の世代。**真偽値のフラグでは足りない。**`speechSynthesis.cancel()` は
   * 直前の発話の `onend` / `onerror` を**非同期で**飛ばすので、cancel の直後に
   * フラグを戻すと、あとから来た古いイベントを「正常終了」と誤読して二重に
   * 次の段落へ進んでしまう。イベント側で自分の世代を照合する。
   */
  let seq = 0;
  let voice = null;
  let voices = [];
  let rate = Number(recall(RATE_KEY, "1")) || 1;

  const state = () => ({
    playing,
    paused: playing && paused,
    index,
    total: blocks.length,
    rate,
    voices,
    voiceName: voice?.name || "",
  });
  const notify = () => onState(state());

  function clearMark() {
    for (const node of root.querySelectorAll(".speaking")) node.classList.remove("speaking");
  }

  function mark(node) {
    clearMark();
    if (!node) return;
    node.classList.add("speaking");
    // 画面外に出ていたら追いかける (読んでいる場所を見失わないように)
    const box = node.getBoundingClientRect();
    if (box.top < 60 || box.bottom > window.innerHeight - 40) {
      node.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  /** ブロック `i` の頭から読む。中は `chunks` に切って 1 つずつ投げる。 */
  function speakAt(i) {
    if (i < 0 || i >= blocks.length) return stopSpeaking();
    index = i;
    mark(blocks[i]);
    chunks = toChunks(blocks[i].textContent.replace(/\s+/g, " ").trim());
    chunkAt = 0;
    seq++;                                    // 走っている発話のイベントを無効化
    speechSynthesis.cancel();                 // 直前が残っていると重なる
    if (!chunks.length) return speakAt(i + 1);
    speakChunk();
  }

  function speakChunk() {
    const mine = ++seq;                       // この発話の世代
    const utter = new SpeechSynthesisUtterance(chunks[chunkAt]);
    if (voice) {
      utter.voice = voice;
      utter.lang = voice.lang;
    }
    utter.rate = rate;
    utter.onend = () => {
      if (mine !== seq) return;               // cancel で流れてきた古いイベント
      if (chunkAt + 1 < chunks.length) {
        chunkAt++;
        speakChunk();                         // 同じ段落の続き (ここでは cancel しない)
      } else if (index + 1 < blocks.length) {
        speakAt(index + 1);
      } else {
        stopSpeaking();
      }
    };
    utter.onerror = (ev) => {
      if (mine !== seq) return;
      // 自分で cancel したときも error が来る。それは失敗ではない
      if (ev.error === "interrupted" || ev.error === "canceled") return;
      stopSpeaking();
    };
    speechSynthesis.speak(utter);
    playing = true;
    paused = false;
    notify();
  }

  function stopSpeaking() {
    seq++;                                    // 以後、古いイベントは全部捨てる
    speechSynthesis.cancel();
    playing = false;
    paused = false;
    index = -1;
    clearMark();
    notify();
  }

  return {
    async prepare() {
      voices = await loadVoices();
      const wanted = recall(VOICE_KEY, "");
      voice = voices.find((v) => v.name === wanted) || voices[0] || null;
      notify();
      return voices;
    },

    /** 本文が入れ替わったら呼ぶ。読み上げ中なら止める。 */
    reset() {
      stopSpeaking();
      blocks = [];
      index = -1;
      notify();
    },

    /** `from` に要素を渡すと、そのブロックから読み始める。 */
    start(from = null) {
      blocks = collectBlocks(root);
      if (!blocks.length) return false;
      const at = from ? blocks.indexOf(from.closest(BLOCKS)) : -1;
      speakAt(at >= 0 ? at : 0);
      return true;
    },

    toggle() {
      if (!playing) return this.start();
      if (paused) speechSynthesis.resume();
      else speechSynthesis.pause();
      paused = !paused;
      notify();
      return true;
    },

    stop: stopSpeaking,

    step(delta) {
      if (!blocks.length) return;
      const next = Math.min(Math.max(index + delta, 0), blocks.length - 1);
      speakAt(next);
    },

    setRate(value) {
      rate = value;
      remember(RATE_KEY, String(value));
      if (playing) speakAt(index);   // 反映には読み直しが要る
      else notify();
    },

    setVoice(name) {
      voice = voices.find((v) => v.name === name) || voice;
      remember(VOICE_KEY, name);
      if (playing) speakAt(index);
      else notify();
    },

    state,
  };
}
