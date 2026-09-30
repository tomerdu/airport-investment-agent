/**
 * Browser speech support for the voice bonus — capability detection and the
 * pure text handling, with no React and no DOM mutation.
 *
 * Two deliberate choices:
 *
 * 1. The Web Speech recognition types are declared here rather than pulled from
 *    `lib.dom.d.ts` or `@types/dom-speech-recognition`. The constructor is still
 *    vendor-prefixed in Chrome and Edge, its presence in the standard lib has
 *    moved between TypeScript releases, and augmenting `Window` would collide
 *    with whatever the installed lib already declares. Local names under our own
 *    prefix cannot conflict, and reading the constructor through one narrow
 *    `unknown` cast keeps `any` out of the file entirely.
 *
 * 2. Interim results are never requested AND never read (see `finalTranscript`).
 *    One of those would do; both means a browser that ignores
 *    `interimResults = false` still cannot put half-heard words in the composer.
 *
 * Recognition runs in the browser. Note that "in the browser" is not the same as
 * "on the device": Chrome and Edge may forward audio to an OS or vendor
 * recognition service. No audio reaches our backend, and we neither record nor
 * store any.
 */

/**
 * Submission scope is English only, set explicitly rather than inherited from
 * the page or the OS. Used for both recognition and synthesis.
 */
export const VOICE_LANG = 'en-US';

/**
 * Hard ceiling on one listening session. The browser's own end-of-speech
 * detection normally fires first; this exists so a hot mic in a noisy room
 * cannot listen indefinitely.
 */
export const LISTEN_LIMIT_MS = 15_000;

// --------------------------------------------------------------- browser types

export interface VoiceAlternative {
  readonly transcript: string;
  readonly confidence: number;
}

export interface VoiceResult {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: VoiceAlternative;
}

export interface VoiceResultList {
  readonly length: number;
  readonly [index: number]: VoiceResult;
}

export interface VoiceRecognitionEvent {
  readonly results: VoiceResultList;
  readonly resultIndex: number;
}

export interface VoiceRecognitionErrorEvent {
  /** 'not-allowed' | 'no-speech' | 'audio-capture' | 'network' | 'aborted' | … */
  readonly error: string;
  readonly message?: string;
}

export interface VoiceRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((event: VoiceRecognitionEvent) => void) | null;
  onerror: ((event: VoiceRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  /** Stop listening and deliver what was recognised. */
  stop(): void;
  /** Stop listening and discard the result. */
  abort(): void;
}

interface VoiceRecognitionCtor {
  new (): VoiceRecognition;
}

// ------------------------------------------------------------ capability check

function recognitionCtor(): VoiceRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: VoiceRecognitionCtor;
    webkitSpeechRecognition?: VoiceRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function speechRecognitionSupported(): boolean {
  return recognitionCtor() !== null;
}

export function speechSynthesisSupported(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as unknown as {
    speechSynthesis?: unknown;
    SpeechSynthesisUtterance?: unknown;
  };
  return (
    typeof w.speechSynthesis === 'object' &&
    w.speechSynthesis !== null &&
    typeof w.SpeechSynthesisUtterance === 'function'
  );
}

/**
 * A configured recognition instance, or null if the browser has no support.
 *
 * A fresh instance per listening session: reusing one across start/stop is
 * unreliable in Chrome, which can leave stale handlers attached.
 */
export function createRecognition(): VoiceRecognition | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;
  const recognition = new Ctor();
  recognition.lang = VOICE_LANG;
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  return recognition;
}

// -------------------------------------------------------------- text handling

/**
 * The recognised text, built from final results only.
 *
 * Reads the whole result list each time rather than the slice from
 * `resultIndex`, so calling it on every `result` event is idempotent — the
 * caller replaces its accumulated value instead of appending, and a browser
 * that fires overlapping events cannot produce duplicated words.
 */
export function finalTranscript(results: VoiceResultList): string {
  const parts: string[] = [];
  for (let i = 0; i < results.length; i++) {
    const result = results[i];
    if (!result || !result.isFinal) continue; // interim text is never used
    const best = result[0];
    if (best && best.transcript) parts.push(best.transcript.trim());
  }
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

/**
 * Merge a transcript into whatever is already typed.
 *
 * Dictation adds to the draft rather than replacing it, so mixing typing and
 * speech in one question works and a stray recognition cannot silently discard
 * something the user wrote.
 */
export function appendTranscript(draft: string, transcript: string): string {
  const spoken = transcript.trim();
  if (!spoken) return draft;
  if (!draft.trim()) return spoken;
  return `${draft.replace(/\s+$/, '')} ${spoken}`;
}

/** Wording for the recognition error codes a user can actually hit. */
export function recognitionErrorMessage(code: string): string | null {
  switch (code) {
    case 'aborted':
      // The user cancelled, or we aborted on unmount. Not a failure to report.
      return null;
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access was blocked. Allow it for this site in the browser settings, or type the question instead.';
    case 'no-speech':
      return 'No speech was detected. Click the microphone and try again.';
    case 'audio-capture':
      return 'No microphone was found.';
    case 'network':
      return 'Speech recognition is unavailable — the browser could not reach its recognition service.';
    case 'language-not-supported':
      return 'This browser has no English (en-US) recognition available.';
    default:
      return 'Speech recognition failed. You can type the question instead.';
  }
}

/**
 * The visible answer as plain prose for speech synthesis.
 *
 * Input is the same markdown string the bubble renders, so only text the user
 * can see is ever spoken — never the tool payloads, the chips, the audit summary
 * or anything else held on the reply object. Mirrors the constructs
 * `renderMarkdown` supports; a table row is read as comma-separated cells.
 */
export function spokenText(markdown: string): string {
  const isDivider = (line: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line);
  const chunks: string[] = [];

  for (const raw of (markdown ?? '').split('\n')) {
    let line = raw.trim();
    if (!line) continue;
    if (isDivider(line)) continue; // |---|---| carries no spoken content

    line = line
      .replace(/^#{1,6}\s+/, '')
      .replace(/^[-*+]\s+/, '')
      .replace(/^\d+[.)]\s+/, '');

    if (line.includes('|')) {
      line = line
        .replace(/^\s*\|/, '')
        .replace(/\|\s*$/, '')
        .split('|')
        .map((cell) => cell.trim())
        .filter(Boolean)
        .join(', ');
    }

    line = line
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*\n]+)\*/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();

    if (line) chunks.push(/[.!?:;,]$/.test(line) ? line : `${line}.`);
  }

  return chunks.join(' ').trim();
}

/**
 * The best available English voice, or null to let the browser choose.
 *
 * Prefers en-US, then any English, then nothing — we do not claim support for
 * languages we have not tested.
 */
export function pickEnglishVoice(
  voices: readonly SpeechSynthesisVoice[],
): SpeechSynthesisVoice | null {
  const english = voices.filter((v) => /^en\b|^en-/i.test(v.lang));
  if (!english.length) return null;
  return (
    english.find((v) => v.lang.toLowerCase() === 'en-us' && v.default) ??
    english.find((v) => v.lang.toLowerCase() === 'en-us') ??
    english.find((v) => v.default) ??
    english[0]
  );
}
