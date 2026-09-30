/**
 * React bindings for the browser speech APIs.
 *
 * All decision logic lives in `voice.ts`; these hooks only own lifecycle —
 * instance creation, the listening ceiling, single-delivery and single-utterance
 * guards, and cleanup on unmount. Neither hook starts anything on mount: voice
 * only ever begins from an explicit user gesture.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { VoiceRecognition } from './voice';
import {
  LISTEN_LIMIT_MS,
  VOICE_LANG,
  createRecognition,
  finalTranscript,
  pickEnglishVoice,
  recognitionErrorMessage,
  speechRecognitionSupported,
  speechSynthesisSupported,
} from './voice';

export interface RecognitionController {
  supported: boolean;
  listening: boolean;
  /** User-facing message for the last failure, or null. */
  error: string | null;
  /** Begin one listening session. No-op while already listening. */
  start: () => void;
  /** End the session and keep what was recognised. */
  stop: () => void;
  /** End the session and discard what was recognised. */
  cancel: () => void;
}

/**
 * One microphone session per `start()`, delivering the final transcript once.
 *
 * `onTranscript` is called at most once per session, never with interim text,
 * and never when the session was cancelled.
 */
export function useSpeechRecognition(
  onTranscript: (text: string) => void,
): RecognitionController {
  const [supported] = useState(speechRecognitionSupported);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<VoiceRecognition | null>(null);
  const timerRef = useRef<number | null>(null);
  const transcriptRef = useRef('');
  const deliveredRef = useRef(false);
  const cancelledRef = useRef(false);

  // Held in a ref so a re-rendered parent cannot leave a stale callback
  // attached to a recognition session that is already running.
  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const settle = useCallback(() => {
    clearTimer();
    recognitionRef.current = null;
    setListening(false);
  }, [clearTimer]);

  const start = useCallback(() => {
    if (recognitionRef.current) return; // already listening
    const recognition = createRecognition();
    if (!recognition) {
      setError('This browser has no speech recognition. Type the question instead.');
      return;
    }

    transcriptRef.current = '';
    deliveredRef.current = false;
    cancelledRef.current = false;
    setError(null);

    recognition.onresult = (event) => {
      // Replace rather than append — finalTranscript() reads the whole result
      // list, so repeated events cannot duplicate words.
      transcriptRef.current = finalTranscript(event.results);
    };

    recognition.onerror = (event) => {
      const message = recognitionErrorMessage(event.error);
      if (message) setError(message);
    };

    recognition.onend = () => {
      const text = transcriptRef.current;
      if (!cancelledRef.current && !deliveredRef.current && text !== '') {
        deliveredRef.current = true; // exactly one delivery per session
        onTranscriptRef.current(text);
      }
      settle();
    };

    recognitionRef.current = recognition;
    setListening(true);

    // Never listen indefinitely. Reaching the ceiling ends the session the same
    // way the Stop button does, keeping whatever was heard.
    timerRef.current = window.setTimeout(() => {
      recognitionRef.current?.stop();
    }, LISTEN_LIMIT_MS);

    try {
      recognition.start();
    } catch {
      // start() throws if a session is somehow already running in this tab.
      recognitionRef.current = null;
      clearTimer();
      setListening(false);
      setError('Could not start the microphone. Try again, or type the question.');
    }
  }, [clearTimer, settle]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop(); // onend then delivers
  }, []);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    const recognition = recognitionRef.current;
    if (recognition) recognition.abort();
    else settle();
    setError(null);
  }, [settle]);

  // Leaving the page must not leave the microphone open.
  useEffect(
    () => () => {
      cancelledRef.current = true;
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return { supported, listening, error, start, stop, cancel };
}

export interface SpeechController {
  supported: boolean;
  /** Id of the turn being spoken, or null when silent. */
  speakingId: string | null;
  speak: (id: string, text: string) => void;
  stop: () => void;
}

/**
 * Read-aloud for one message at a time.
 *
 * A single instance owns playback for the whole conversation, so "speaking"
 * state cannot get stuck on a message that was interrupted by another. Nothing
 * here is called automatically — there is no autoplay path.
 */
export function useSpeechSynthesis(): SpeechController {
  const [supported] = useState(speechSynthesisSupported);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (!supported) return;
    const synth = window.speechSynthesis;
    // The voice list is usually empty on first paint and arrives asynchronously.
    const load = () => {
      voicesRef.current = synth.getVoices();
    };
    load();
    synth.addEventListener('voiceschanged', load);
    return () => {
      synth.removeEventListener('voiceschanged', load);
      synth.cancel(); // never keep talking after the view is gone
    };
  }, [supported]);

  const stop = useCallback(() => {
    if (supported) window.speechSynthesis.cancel();
    setSpeakingId(null);
  }, [supported]);

  const speak = useCallback(
    (id: string, text: string) => {
      if (!supported) return;
      const synth = window.speechSynthesis;
      // One utterance at a time: starting another cancels the previous one.
      synth.cancel();

      const content = text.trim();
      if (!content) {
        setSpeakingId(null);
        return;
      }

      const utterance = new SpeechSynthesisUtterance(content);
      utterance.lang = VOICE_LANG;
      const voices = voicesRef.current.length ? voicesRef.current : synth.getVoices();
      const voice = pickEnglishVoice(voices);
      if (voice) utterance.voice = voice; // otherwise the browser default

      const release = () => setSpeakingId((current) => (current === id ? null : current));
      utterance.onend = release;
      utterance.onerror = release;

      setSpeakingId(id);
      synth.speak(utterance);
    },
    [supported],
  );

  return { supported, speakingId, speak, stop };
}
