/**
 * Tests for the voice bonus logic — Node's built-in test runner, no dependencies.
 *
 *   cd frontend
 *   npm.cmd run test:voice
 *
 * Deliberately outside `src/`, so neither tsconfig project compiles it and it
 * cannot affect `npm run build`.
 *
 * Scope: everything in `voice.ts`, which is where the behaviour that matters
 * lives — capability detection, final-only transcripts, error wording, and the
 * markdown-to-speech extraction. The two hooks in `useVoice.ts` own only React
 * lifecycle and need a DOM and a renderer to exercise, which this project has no
 * setup for; they are covered by the manual browser checklist in the README.
 */

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import type { VoiceResultList } from '../src/voice.ts';
import {
  LISTEN_LIMIT_MS,
  VOICE_LANG,
  appendTranscript,
  createRecognition,
  finalTranscript,
  pickEnglishVoice,
  recognitionErrorMessage,
  speechRecognitionSupported,
  speechSynthesisSupported,
  spokenText,
} from '../src/voice.ts';

// --------------------------------------------------------------------- helpers

/** A stand-in for the browser global; `voice.ts` reads `window` lazily. */
function setWindow(value: unknown): void {
  (globalThis as unknown as { window?: unknown }).window = value;
}

function clearWindow(): void {
  delete (globalThis as unknown as { window?: unknown }).window;
}

class FakeRecognition {
  lang = '';
  continuous = true;
  interimResults = true;
  maxAlternatives = 0;
  onstart: unknown = null;
  onresult: unknown = null;
  onerror: unknown = null;
  onend: unknown = null;
  start(): void {}
  stop(): void {}
  abort(): void {}
}

/** Shapes a SpeechRecognitionResultList closely enough for finalTranscript. */
function results(
  entries: ReadonlyArray<{ text: string; isFinal: boolean }>,
): VoiceResultList {
  const list: Record<string, unknown> = { length: entries.length };
  entries.forEach((entry, i) => {
    list[String(i)] = {
      isFinal: entry.isFinal,
      length: 1,
      0: { transcript: entry.text, confidence: 0.9 },
    };
  });
  return list as unknown as VoiceResultList;
}

/** A SpeechSynthesisVoice stand-in; the DOM type is unavailable under Node. */
type FakeVoice = Parameters<typeof pickEnglishVoice>[0][number];

function voice(lang: string, isDefault = false): FakeVoice {
  return {
    lang,
    default: isDefault,
    name: lang,
    localService: true,
    voiceURI: lang,
  } as unknown as FakeVoice;
}

afterEach(clearWindow);

// ------------------------------------------------------------------ detection

describe('capability detection', () => {
  it('reports unsupported when there is no window at all', () => {
    clearWindow();
    assert.equal(speechRecognitionSupported(), false);
    assert.equal(speechSynthesisSupported(), false);
  });

  it('reports unsupported when the browser has neither constructor', () => {
    setWindow({});
    assert.equal(speechRecognitionSupported(), false);
    assert.equal(createRecognition(), null);
  });

  it('detects the standard constructor', () => {
    setWindow({ SpeechRecognition: FakeRecognition });
    assert.equal(speechRecognitionSupported(), true);
  });

  it('detects the webkit-prefixed constructor used by Chrome and Edge', () => {
    setWindow({ webkitSpeechRecognition: FakeRecognition });
    assert.equal(speechRecognitionSupported(), true);
  });

  it('requires both speechSynthesis and the utterance constructor', () => {
    setWindow({ speechSynthesis: {} });
    assert.equal(speechSynthesisSupported(), false, 'utterance constructor missing');

    setWindow({ SpeechSynthesisUtterance: function () {} });
    assert.equal(speechSynthesisSupported(), false, 'speechSynthesis missing');

    setWindow({ speechSynthesis: {}, SpeechSynthesisUtterance: function () {} });
    assert.equal(speechSynthesisSupported(), true);
  });
});

describe('recognition configuration', () => {
  it('pins English and one non-continuous, final-only session', () => {
    setWindow({ SpeechRecognition: FakeRecognition });
    const recognition = createRecognition();
    assert.ok(recognition);
    assert.equal(recognition.lang, VOICE_LANG);
    assert.equal(recognition.lang, 'en-US');
    assert.equal(recognition.continuous, false, 'must not listen continuously');
    assert.equal(recognition.interimResults, false, 'must not request interim text');
    assert.equal(recognition.maxAlternatives, 1);
  });

  it('has a finite listening ceiling', () => {
    assert.ok(LISTEN_LIMIT_MS > 0 && LISTEN_LIMIT_MS <= 30_000);
  });

  it('returns a fresh instance each call', () => {
    setWindow({ SpeechRecognition: FakeRecognition });
    assert.notEqual(createRecognition(), createRecognition());
  });
});

// ----------------------------------------------------------------- transcript

describe('finalTranscript', () => {
  it('returns the final text', () => {
    assert.equal(
      finalTranscript(results([{ text: 'compare LAX and SNA congestion', isFinal: true }])),
      'compare LAX and SNA congestion',
    );
  });

  it('ignores interim results entirely', () => {
    assert.equal(finalTranscript(results([{ text: 'comp', isFinal: false }])), '');
    assert.equal(
      finalTranscript(
        results([
          { text: 'rank New England airports', isFinal: true },
          { text: 'by dem', isFinal: false },
        ]),
      ),
      'rank New England airports',
      'interim tail must not reach the composer',
    );
  });

  it('joins several final segments', () => {
    assert.equal(
      finalTranscript(
        results([
          { text: 'what is the TDPI', isFinal: true },
          { text: 'for Boston', isFinal: true },
        ]),
      ),
      'what is the TDPI for Boston',
    );
  });

  it('is idempotent, so repeated events cannot duplicate words', () => {
    const list = results([{ text: 'unmet demand at SFO', isFinal: true }]);
    assert.equal(finalTranscript(list), finalTranscript(list));
    assert.equal(finalTranscript(list), 'unmet demand at SFO');
  });

  it('collapses whitespace and handles an empty list', () => {
    assert.equal(finalTranscript(results([])), '');
    assert.equal(finalTranscript(results([{ text: '  spaced   out  ', isFinal: true }])), 'spaced out');
  });
});

describe('appendTranscript', () => {
  it('fills an empty draft', () => {
    assert.equal(appendTranscript('', 'rank Maine airports'), 'rank Maine airports');
    assert.equal(appendTranscript('   ', 'rank Maine airports'), 'rank Maine airports');
  });

  it('adds to an existing draft rather than replacing it', () => {
    assert.equal(appendTranscript('Compare BOS', 'and BGR'), 'Compare BOS and BGR');
    assert.equal(appendTranscript('Compare BOS ', 'and BGR'), 'Compare BOS and BGR');
  });

  it('leaves the draft untouched when nothing was recognised', () => {
    assert.equal(appendTranscript('Compare BOS', ''), 'Compare BOS');
    assert.equal(appendTranscript('Compare BOS', '   '), 'Compare BOS');
  });

  it('does not truncate — the backend limit applies exactly as for typed text', () => {
    const long = 'a'.repeat(5000);
    assert.equal(appendTranscript('', long).length, 5000);
  });
});

describe('recognitionErrorMessage', () => {
  it('stays silent on a deliberate cancellation', () => {
    assert.equal(recognitionErrorMessage('aborted'), null);
  });

  it('explains a blocked microphone and offers the typed path', () => {
    for (const code of ['not-allowed', 'service-not-allowed']) {
      const message = recognitionErrorMessage(code);
      assert.ok(message);
      assert.match(message, /blocked/i);
      assert.match(message, /type/i);
    }
  });

  it('covers no-speech, no device and network failure distinctly', () => {
    const distinct = new Set(
      ['no-speech', 'audio-capture', 'network'].map((c) => recognitionErrorMessage(c)),
    );
    assert.equal(distinct.size, 3);
  });

  it('falls back to a generic message that still mentions typing', () => {
    const message = recognitionErrorMessage('something-new-in-chrome-2027');
    assert.ok(message);
    assert.match(message, /type/i);
  });
});

// ----------------------------------------------------------- speech synthesis

describe('spokenText', () => {
  it('drops heading and bullet markers', () => {
    assert.equal(spokenText('## Findings'), 'Findings.');
    assert.equal(spokenText('- BOS leads the cohort'), 'BOS leads the cohort.');
    assert.equal(spokenText('1. BOS'), 'BOS.');
  });

  it('drops emphasis and code markers', () => {
    assert.equal(spokenText('**TDPI** is a *proxy*, see `engine`'), 'TDPI is a proxy, see engine.');
  });

  it('reads a table row as comma-separated cells and skips the divider', () => {
    const table = ['| Airport | TDPI |', '| --- | --- |', '| BOS | 58.6 |'].join('\n');
    assert.equal(spokenText(table), 'Airport, TDPI. BOS, 58.6.');
  });

  it('leaves no markdown punctuation to be read aloud', () => {
    const answer = [
      '### Terminal demand',
      '',
      '**BOS** scores `58.6` — the highest in the cohort.',
      '',
      '- *Load factor*: 0.87',
      '| Airport | Score |',
      '| --- | --- |',
      '| BOS | 58.6 |',
    ].join('\n');
    const spoken = spokenText(answer);
    assert.doesNotMatch(spoken, /[#*`|]/, 'markdown syntax reached the synthesiser');
    assert.match(spoken, /BOS scores 58\.6/);
    assert.match(spoken, /Load factor/);
  });

  it('returns nothing for empty or whitespace-only input', () => {
    assert.equal(spokenText(''), '');
    assert.equal(spokenText('\n\n   \n'), '');
  });

  it('does not double up existing sentence punctuation', () => {
    assert.equal(spokenText('It is suppressed.'), 'It is suppressed.');
    assert.doesNotMatch(spokenText('It is suppressed.'), /\.\./);
  });
});

describe('pickEnglishVoice', () => {
  it('returns null when no English voice exists, letting the browser choose', () => {
    assert.equal(pickEnglishVoice([voice('he-IL'), voice('fr-FR')]), null);
    assert.equal(pickEnglishVoice([]), null);
  });

  it('prefers a default en-US voice', () => {
    const wanted = voice('en-US', true);
    const picked = pickEnglishVoice([voice('en-GB', true), voice('en-US'), wanted]);
    assert.equal(picked, wanted);
  });

  it('falls back to any en-US, then any English', () => {
    const enUS = voice('en-US');
    assert.equal(pickEnglishVoice([voice('en-AU'), enUS]), enUS);

    const enGB = voice('en-GB');
    assert.equal(pickEnglishVoice([voice('de-DE'), enGB]), enGB);
  });

  it('never picks a non-English voice', () => {
    const picked = pickEnglishVoice([voice('he-IL', true), voice('en-IN')]);
    assert.ok(picked);
    assert.match(picked.lang, /^en/i);
  });
});
