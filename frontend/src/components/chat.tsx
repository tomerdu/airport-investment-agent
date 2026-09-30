import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChatTurn } from '../types';
import { renderMarkdown } from '../markdown';
import type { SpeechController } from '../useVoice';
import { useSpeechRecognition } from '../useVoice';
import { appendTranscript, spokenText } from '../voice';

/** The four assignment questions, immediately runnable. */
export const SUGGESTED = [
  {
    q: 'Which airports in New England are strong candidates for terminal expansion?',
    why: 'Ranks a regional cohort by demand pressure, with suppression handled explicitly',
  },
  {
    q: 'Compare LA and Santa Ana airport congestion levels.',
    why: 'Separates volume from per-flight intensity; states the LA → LAX reading',
  },
  {
    q: 'What is the percentage of long haul flights out of Anchorage airport?',
    why: 'Threshold sensitivity, and passenger vs cargo split',
  },
  {
    q: 'What is the unmet flight demand in SFO airport and why?',
    why: 'Proxy evidence only — no fabricated quantity',
  },
];

const FOLLOW_UPS = [
  'Why is the second one ranked there?',
  'What if we used 1,500 miles as the long-haul threshold instead?',
  'Now add Burbank to that congestion comparison.',
  'What assumptions have you made so far?',
];

export function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="empty">
      <h2>Airport investment screening</h2>
      <p>
        Ask about US airport demand pressure, airside congestion, long-haul mix or
        supply constraints. Every figure is computed by a deterministic engine —
        the assistant narrates, it never calculates.
      </p>
      <div className="suggestions">
        {SUGGESTED.map((s, i) => (
          <button className="suggestion" key={s.q} onClick={() => onPick(s.q)}>
            <span className="idx">{i + 1}</span>
            <span>
              <span className="q">{s.q}</span>
              <span className="why">{s.why}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function FollowUpChips({
  onPick,
  disabled,
}: {
  onPick: (q: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="turn-meta" style={{ marginBottom: 14 }}>
      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Try a follow-up:</span>
      {FOLLOW_UPS.map((f) => (
        <button
          key={f}
          className="chip"
          disabled={disabled}
          onClick={() => onPick(f)}
          style={{ cursor: disabled ? 'not-allowed' : 'pointer' }}
        >
          {f}
        </button>
      ))}
    </div>
  );
}

export function Turn({
  turn,
  speech,
}: {
  turn: ChatTurn;
  /** Read-aloud controller, owned by App so only one answer speaks at a time. */
  speech?: SpeechController;
}) {
  if (turn.role === 'user') {
    return (
      <div className="turn user">
        <div className="bubble user">{turn.text}</div>
      </div>
    );
  }

  if (turn.error) {
    return (
      <div className="turn">
        <div className="bubble error">
          <b>Could not complete that request.</b>
          <div style={{ marginTop: 5, fontSize: 12.5 }}>{turn.error}</div>
        </div>
      </div>
    );
  }

  const reply = turn.reply;
  const canSpeak = !!speech?.supported && turn.text.trim() !== '';
  const speaking = speech?.speakingId === turn.id;

  return (
    <div className="turn">
      <div className="bubble assistant">
        <div
          className="md"
          // Rendered by the local sanitising markdown renderer in markdown.ts,
          // which emits only a fixed tag set and escapes everything else.
          dangerouslySetInnerHTML={{ __html: renderMarkdown(turn.text) }}
        />
        {(reply || canSpeak) && (
          <div className="turn-meta">
            {reply && (
              <>
                {reply.tool_calls.map((t, i) => (
                  <span
                    className={`chip tool${t.ok ? '' : ' warn'}`}
                    key={`${t.name}-${i}`}
                    title={t.error ?? `${t.duration_ms} ms`}
                  >
                    {t.ok ? '✓' : '✕'} {t.name}
                  </span>
                ))}
                <span
                  className={`chip ${reply.audit.passed ? 'ok' : 'warn'}`}
                  title={reply.audit.summary}
                >
                  {reply.audit.passed ? '✓' : '⚠'} {reply.audit.numerals_checked} figures
                  verified
                </span>
                {reply.degraded && (
                  <span className="chip warn" title="The answer was regenerated after a provenance check">
                    regenerated
                  </span>
                )}
              </>
            )}
            {speech && canSpeak && (
              <button
                type="button"
                className={`chip speak${speaking ? ' speaking' : ''}`}
                aria-pressed={speaking}
                // Speaks the visible answer text only — never the tool payloads,
                // the chips above or anything else on the reply object.
                onClick={() =>
                  speaking ? speech.stop() : speech.speak(turn.id, spokenText(turn.text))
                }
                title={
                  speaking
                    ? 'Stop reading this answer aloud'
                    : 'Read this answer aloud (English)'
                }
              >
                <span aria-hidden="true">{speaking ? '◼' : '▶'}</span>
                {speaking ? 'Stop reading' : 'Read answer'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function Composer({
  onSend,
  busy,
  offline = false,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  /** Backend unreachable — distinct from "a request is in flight". */
  offline?: boolean;
}) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  const disabled = busy || offline;

  // Dictation lands in the draft rather than being sent. The user reads it,
  // edits it if the recogniser misheard, and presses Send — so a transcript
  // takes exactly the same path as typed text, with the same length limit and
  // the same duplicate-send guard, and nothing is ever submitted unreviewed.
  const acceptTranscript = useCallback((text: string) => {
    setValue((current) => appendTranscript(current, text));
    ref.current?.focus();
  }, []);

  const voice = useSpeechRecognition(acceptTranscript);

  useEffect(() => {
    if (!disabled) ref.current?.focus();
  }, [disabled]);

  function submit() {
    const text = value.trim();
    if (!text || disabled) return;
    if (voice.listening) voice.cancel();
    onSend(text);
    setValue('');
  }

  const micLabel = !voice.supported
    ? 'Voice input is not supported in this browser'
    : voice.listening
      ? 'Stop listening'
      : 'Ask by voice';

  return (
    <div
      className="composer-wrap"
      onKeyDown={(e) => {
        // Escape abandons a listening session and discards the transcript.
        if (e.key === 'Escape' && voice.listening) {
          e.preventDefault();
          voice.cancel();
        }
      }}
    >
      <div className="composer">
        <textarea
          ref={ref}
          value={value}
          disabled={offline}
          placeholder={
            offline
              ? 'Backend unavailable — start it on port 8000 to ask a question'
              : 'Ask about an airport, a region, congestion, long-haul mix…'
          }
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          aria-label="Message"
        />
        <button
          type="button"
          className={`mic-btn${voice.listening ? ' listening' : ''}`}
          onClick={() => (voice.listening ? voice.stop() : voice.start())}
          disabled={!voice.supported || offline}
          aria-pressed={voice.listening}
          aria-label={micLabel}
          title={micLabel}
        >
          {voice.listening ? (
            <span aria-hidden="true">◼</span>
          ) : (
            <svg
              aria-hidden="true"
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <rect x="9" y="2" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0" />
              <path d="M12 18v3" />
            </svg>
          )}
        </button>
        <button
          className="send-btn"
          onClick={submit}
          disabled={disabled || !value.trim()}
        >
          {offline ? 'Offline' : busy ? 'Working…' : 'Send'}
        </button>
      </div>

      {/*
        Listening state and errors are words, not just a coloured icon, and the
        region is live so a screen reader announces the change.
      */}
      <div className="voice-note" role="status" aria-live="polite">
        {voice.listening ? (
          <span className="listening-state">
            <span className="mic-pulse" aria-hidden="true" />
            Listening in English — speak your question, then press Stop (or Esc to
            discard).
          </span>
        ) : voice.error ? (
          <span className="voice-error">{voice.error}</span>
        ) : !voice.supported ? (
          <span>
            Voice input needs Chrome or Edge on desktop. Typing works everywhere.
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function Thinking() {
  return (
    <div className="turn">
      <div className="bubble assistant">
        <div className="thinking">
          <span className="spinner" />
          Querying the analytics engine…
        </div>
        <div style={{ marginTop: 10 }}>
          <div className="skeleton" style={{ width: '82%' }} />
          <div className="skeleton" style={{ width: '94%' }} />
          <div className="skeleton" style={{ width: '61%' }} />
        </div>
      </div>
    </div>
  );
}
