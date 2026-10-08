// Topic scope -- one piece of state shared by all customer-side modules.
//
// Used to live in pages/BrandGrowth/shell.tsx; the report center and task monitoring already imported it for reuse
// (Reports/shell.tsx wraps its own TopicPicker around this hook). After the brand growth module was retired
// it was moved permanently to hooks/ -- it was always a site-wide shared piece, just in the wrong directory.
//
// Conventions:
//   - the selected topic lives in the URL's ?topic=<id>, not in state, so any subpage link carries the context;
//   - engine multi-select uses ?engines=a,b; empty / absent = select-all semantics;
//   - when an ops role's ?topic= points to someone else's topic, that one is resolved separately; isForeign lets pages label "customer view".

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { aiTelemetryApi, ALL_TIME_PERIOD, type Topic } from '../services/aiTelemetryApi';
import { useAuth } from '../contexts/AuthContext';

export interface ShellState {
  token: string;
  topics: Topic[];
  topic: Topic | null;
  topicId: number | null;
  setTopicId: (id: number) => void;
  // All-time metric definition (since launch) -- no more 7/30/90 toggle, always pass ALL_TIME_PERIOD
  period: number;
  // Model multi-select (2026-05-21): empty array = select-all/aggregate semantics
  selectedEngines: string[];
  setSelectedEngines: (es: string[]) => void;
  loading: boolean;
  // Admin is viewing a customer topic not their own (?topic= points to someone else) -> add a "customer view" hint in the header
  isForeign: boolean;
}

export function useShellState(): ShellState {
  const [searchParams, setSearchParams] = useSearchParams();
  const { isStaff } = useAuth();
  const token = localStorage.getItem('token') || '';
  const [ownTopics, setOwnTopics] = useState<Topic[]>([]);
  // The topic resolved separately when workbench staff (admin | operator) view someone else's topic (?topic= points to a non-owned topic)
  const [extraTopic, setExtraTopic] = useState<Topic | null>(null);
  const [loadingOwn, setLoadingOwn] = useState(true);
  const [resolving, setResolving] = useState(false);
  const loading = loadingOwn || resolving;

  const urlTopic = Number(searchParams.get('topic') || '0');
  // period is only still used by count metrics like "total citations / sources / platforms"; the six metrics' window is decided by the backend per the report center's
  // metric definition (latest period · weekly), unaffected by whatever the frontend passes; don't treat it as a global time window
  const period = ALL_TIME_PERIOD;
  // With extraTopic, expose only this customer topic (the picker shows the customer topic name, clear semantics); otherwise use the owned list
  const topics = extraTopic ? [extraTopic] : ownTopics;
  const topicId = urlTopic > 0 ? urlTopic : (topics[0]?.id ?? null);
  const topic = topics.find(t => t.id === topicId) || null;

  // engines param: comma-separated; empty / absent = select-all semantics
  const urlEnginesRaw = searchParams.get('engines') || '';
  const urlEngines = urlEnginesRaw
    .split(',').map(s => s.trim()).filter(Boolean);
  // Intersect with topic.engines as a fallback -- after switching topics the previous selection may no longer apply
  const topicEngines = topic?.engines ?? [];
  const selectedEngines = topicEngines.length === 0
    ? urlEngines
    : urlEngines.filter(e => topicEngines.includes(e as Topic['engines'][number]));

  useEffect(() => {
    if (!token) { setLoadingOwn(false); return; }
    setLoadingOwn(true);
    aiTelemetryApi.listTopics(token)
      .then(list => setOwnTopics(list))
      .catch(() => setOwnTopics([]))
      .finally(() => setLoadingOwn(false));
  }, [token]);

  // Workbench staff accessing a topic not their own -> resolve that topic separately. All subpages use the same ?topic=<id>,
  // so with this unified fallback here, the whole panel set (including drill-down subpages) can see customer data, without passing params link by link.
  useEffect(() => {
    if (loadingOwn) return;  // wait for the owned list to load before judging, to avoid mistaking an owned topic for a foreign one
    if (!token || !isStaff || urlTopic <= 0 || ownTopics.some(t => t.id === urlTopic)) {
      setExtraTopic(null);
      setResolving(false);
      return;
    }
    let cancelled = false;
    setResolving(true);
    aiTelemetryApi.getTopic(urlTopic, token)
      .then(t => { if (!cancelled) setExtraTopic(t); })
      .catch(() => { if (!cancelled) setExtraTopic(null); })
      .finally(() => { if (!cancelled) setResolving(false); });
    return () => { cancelled = true; };
  }, [token, isStaff, urlTopic, ownTopics, loadingOwn]);

  const setTopicId = (id: number) => {
    const next = new URLSearchParams(searchParams);
    next.set('topic', String(id));
    setSearchParams(next, { replace: true });
  };
  const setSelectedEngines = (es: string[]) => {
    const next = new URLSearchParams(searchParams);
    if (es.length === 0) next.delete('engines');
    else next.set('engines', es.join(','));
    setSearchParams(next, { replace: true });
  };

  return {
    token, topics, topic, topicId, setTopicId, period,
    selectedEngines, setSelectedEngines, loading,
    isForeign: extraTopic != null,
  };
}
