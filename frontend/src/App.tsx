import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { Composer, EmptyState, FollowUpChips, Thinking, Turn } from './components/chat';
import {
  ComparePanel,
  LongHaulPanel,
  ProfilePanel,
  RankPanel,
  SourcesPanel,
  UnmetDemandPanel,
} from './components/panels';
import { useSpeechSynthesis } from './useVoice';
import type {
  AgentReply,
  AirportProfileResult,
  ChatTurn,
  CompareResult,
  HealthResponse,
  LongHaulResult,
  RankResult,
  UnmetDemandResult,
} from './types';

let seq = 0;
const nextId = () => `t${++seq}`;

/** Tools whose results the analytics pane renders. */
const PANEL_TOOLS = new Set([
  'get_airport_profile',
  'rank_airports',
  'compare_airports',
  'long_haul_breakdown',
  'unmet_demand_evidence',
]);

/**
 * Pick the structured payloads out of the reply's tool calls.
 *
 * The analytics pane renders from these objects, never from the model's prose —
 * so a chart or table can never disagree with the engine.
 */
function useAnalytics(reply: AgentReply | null) {
  return useMemo(() => {
    const found = {
      profile: null as AirportProfileResult | null,
      rank: null as RankResult | null,
      compare: null as CompareResult | null,
      longHaul: null as LongHaulResult | null,
      unmet: null as UnmetDemandResult | null,
    };
    if (!reply) return found;
    for (const call of reply.tool_calls) {
      if (!call.ok || !call.result) continue;
      switch (call.name) {
        case 'get_airport_profile':
          found.profile = call.result as AirportProfileResult;
          break;
        case 'rank_airports':
          found.rank = call.result as RankResult;
          break;
        case 'compare_airports':
          found.compare = call.result as CompareResult;
          break;
        case 'long_haul_breakdown':
          found.longHaul = call.result as LongHaulResult;
          break;
        case 'unmet_demand_evidence':
          found.unmet = call.result as UnmetDemandResult;
          break;
      }
    }
    return found;
  }, [reply]);
}

export default function App() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [lastReply, setLastReply] = useState<AgentReply | null>(null);
  // A follow-up that answers from earlier context calls no tools. Without a
  // separate hold, the analytics pane would blank out mid-conversation, which
  // reads as "the data went away". Keep the last reply that actually produced
  // panels and mark it as carried over.
  const [lastAnalyticsReply, setLastAnalyticsReply] = useState<AgentReply | null>(
    null,
  );
  const [analyticsStale, setAnalyticsStale] = useState(false);

  const chatEndRef = useRef<HTMLDivElement>(null);

  // One read-aloud controller for the whole conversation, so starting a second
  // answer stops the first and no message is left stuck showing "Stop reading".
  // Nothing calls speak() automatically — playback is always a button press.
  const speech = useSpeechSynthesis();
  // `speech` is a fresh object each render, so `reset` depends on the method
  // rather than the container — stop() is a useCallback over state that never
  // changes, so this keeps reset memoised on sessionId alone.
  const stopSpeaking = speech.stop;

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((e: ApiError) => setHealthError(e.message));
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns, busy]);

  const send = useCallback(
    async (text: string) => {
      setTurns((t) => [...t, { id: nextId(), role: 'user', text }]);
      setBusy(true);
      try {
        const reply = await api.chat(text, sessionId);
        setSessionId(reply.session_id);
        setLastReply(reply);
        const producedPanels = reply.tool_calls.some(
          (c) => c.ok && PANEL_TOOLS.has(c.name),
        );
        if (producedPanels) {
          setLastAnalyticsReply(reply);
          setAnalyticsStale(false);
        } else {
          setAnalyticsStale((prev) => prev || lastAnalyticsReply !== null);
        }
        setTurns((t) => [
          ...t,
          { id: nextId(), role: 'assistant', text: reply.answer, reply },
        ]);
      } catch (e) {
        const message =
          e instanceof ApiError ? e.message : 'Unexpected error contacting the backend.';
        setTurns((t) => [
          ...t,
          { id: nextId(), role: 'assistant', text: '', error: message },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [sessionId],
  );

  const reset = useCallback(async () => {
    stopSpeaking(); // don't keep reading an answer that is no longer on screen
    if (sessionId) await api.resetSession(sessionId).catch(() => undefined);
    setTurns([]);
    setLastReply(null);
    setLastAnalyticsReply(null);
    setAnalyticsStale(false);
    setSessionId(null);
  }, [sessionId, stopSpeaking]);

  const analytics = useAnalytics(lastAnalyticsReply);
  const hasAnalytics =
    analytics.profile ||
    analytics.rank ||
    analytics.compare ||
    analytics.longHaul ||
    analytics.unmet;

  const windowLabel = health?.window ?? lastReply?.window ?? '—';

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Airport Investment Intelligence</h1>
          <p className="sub">Deterministic screening for US airport modernisation</p>
        </div>

        <div className="masthead-spacer" />

        <span className="window-badge" title="Every figure on this page comes from this window">
          <span className="label">Analysis window</span>
          <b>{windowLabel}</b>
        </span>

        <span className="health">
          <span
            className={`status-dot${healthError ? ' down' : health?.llm_configured ? '' : ' warn'}`}
            aria-hidden="true"
          />
          <span className="detail">
            {healthError
              ? 'backend offline'
              : health
                ? `${health.airports} airports · ${health.model}`
                : 'connecting…'}
          </span>
        </span>

        {turns.length > 0 && (
          <button className="ghost-btn" onClick={reset} disabled={busy}>
            New session
          </button>
        )}
      </header>

      <div className="workspace">
        <section className="pane chat" aria-label="Conversation">
          <div className="pane-head">
            Conversation
            {sessionId && (
              <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>
                session {sessionId.slice(0, 8)} · {turns.filter((t) => t.role === 'user').length} turns
              </span>
            )}
          </div>

          <div className="pane-body">
            {healthError && (
              <div className="banner error">
                <b>Backend unreachable.</b>
                <div style={{ marginTop: 4 }}>{healthError}</div>
              </div>
            )}
            {health && !health.llm_configured && (
              <div className="banner warn">
                <b>No API key configured.</b> Chat is disabled; the analytics
                endpoints still work. Add <code>ANTHROPIC_API_KEY</code> to{' '}
                <code>.env</code> and restart the backend.
              </div>
            )}

            {turns.length === 0 && !healthError ? (
              <EmptyState onPick={send} />
            ) : (
              <>
                {turns.map((t) => (
                  <Turn key={t.id} turn={t} speech={speech} />
                ))}
                {busy && <Thinking />}
                {!busy && turns.length > 0 && (
                  <FollowUpChips onPick={send} disabled={busy} />
                )}
              </>
            )}
            <div ref={chatEndRef} />
          </div>

          <Composer onSend={send} busy={busy} offline={!!healthError} />
        </section>

        <section className="pane analytics" aria-label="Analytics">
          <div className="pane-head">
            Deterministic analytics
            <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>
              rendered from engine output, not from the answer text
            </span>
          </div>

          <div className="pane-body">
            {!hasAnalytics && !busy && (
              <div className="placeholder">
                <div className="big" aria-hidden="true">◧</div>
                <h3>No analysis yet</h3>
                <p>
                  Ask a question and the score breakdowns, comparison tables and
                  source attribution will appear here.
                </p>
              </div>
            )}

            {busy && !hasAnalytics && (
              <div className="card">
                <div className="card-body">
                  <div className="skeleton" style={{ width: '40%', height: 14 }} />
                  <div className="skeleton" style={{ width: '100%', height: 30 }} />
                  <div className="skeleton" style={{ width: '100%', height: 30 }} />
                  <div className="skeleton" style={{ width: '70%' }} />
                </div>
              </div>
            )}

            {hasAnalytics && analyticsStale && (
              <div className="banner warn">
                <b>Showing the previous analysis.</b> The latest answer was
                drawn from context already retrieved, so no new figures were
                computed.
              </div>
            )}

            {analytics.rank && <RankPanel data={analytics.rank} />}
            {analytics.compare && <ComparePanel data={analytics.compare} />}
            {analytics.profile && <ProfilePanel data={analytics.profile} />}
            {analytics.longHaul && <LongHaulPanel data={analytics.longHaul} />}
            {analytics.unmet && <UnmetDemandPanel data={analytics.unmet} />}

            {(lastAnalyticsReply || lastReply) && (
              <SourcesPanel
                sources={(lastAnalyticsReply ?? lastReply)!.sources}
                limitations={(lastAnalyticsReply ?? lastReply)!.limitations}
                // Assumptions accumulate across the session, so always take
                // the newest reply's list.
                assumptions={(lastReply ?? lastAnalyticsReply)!.assumptions}
              />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
