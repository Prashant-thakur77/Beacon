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
export function speakFallback(text: string, onEnd: () => void): void {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.onend = onEnd;
    u.onerror = onEnd;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  } catch {
    onEnd();
  }
}
