/** Plays Polly mp3 and fires sentence callbacks from speech marks so the transcript lights up in sync. */
export class Player {
  private audio: HTMLAudioElement | null = null;
  private timers: number[] = [];

  async play(
    audioB64: string,
    marks: Array<{ time: number; value: string }>,
    onSentence: (index: number) => void,
    onEnd: () => void,
  ): Promise<void> {
    this.stop();
    const audio = new Audio(`data:audio/mpeg;base64,${audioB64}`);
    this.audio = audio;
    audio.onended = () => {
      this.clearTimers();
      onEnd();
    };
    audio.onerror = () => {
      this.clearTimers();
      onEnd();
    };
    try {
      await audio.play();
    } catch {
      onEnd();
      return;
    }
    marks.forEach((m, i) => {
      this.timers.push(window.setTimeout(() => onSentence(i), Math.max(0, m.time)));
    });
    if (marks.length === 0) onSentence(0);
  }

  stop(): void {
    this.clearTimers();
    if (this.audio) {
      this.audio.pause();
      this.audio.src = "";
      this.audio = null;
    }
  }

  private clearTimers() {
    this.timers.forEach((t) => window.clearTimeout(t));
    this.timers = [];
  }
}

/** Browser TTS fallback when Polly returns no audio (or in replay). */
/**
 * Browser speech synthesis when Polly audio is absent (local mode, or a Polly
 * failure). Some browsers have no voices and never fire `onend`, so a watchdog
 * sized to the text length ends the turn regardless — the console must never
 * hang in "speaking".
 */
export function speakFallback(text: string, onEnd: () => void): void {
  let ended = false;
  const finish = () => {
    if (ended) return;
    ended = true;
    window.clearTimeout(watchdog);
    onEnd();
  };
  const words = text.split(/\s+/).filter(Boolean).length;
  const watchdog = window.setTimeout(finish, Math.min(20_000, 1500 + words * 380));
  try {
    if (!("speechSynthesis" in window) || window.speechSynthesis.getVoices().length === 0) {
      // no engine: keep the reading pace so sentence highlighting still makes sense
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.onend = finish;
    u.onerror = finish;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  } catch {
    finish();
  }
}
