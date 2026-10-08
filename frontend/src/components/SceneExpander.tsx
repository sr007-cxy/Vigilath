// 2026-05-28 -- 4-dimension scene expansion component.
// Single seed input + 4 scene checkboxes (all checked by default) + [Expand] button.
// After expanding, 4 tabs render below, each listing the candidate queries produced for that scene.
// Selections call back to the outer layer (which decides whether to send to probe / add to selected prompts).

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SceneType } from '../services/aiTelemetryApi';
import { ALL_SCENES } from '../services/aiTelemetryApi';
import type { ExpandQueriesResp } from '../services/topicProfileApi';
import type { BrandProduct } from '../services/aiTelemetryApi';
import { MarkSeedAsProduct } from './MarkSeedAsProduct';

interface Props {
  /** Whether the current topic requires a non-empty target (brand name) -- the brand scene is grayed out when target is empty */
  brandSceneAvailable: boolean;
  /** Expansion callback; the parent decides which API client to call. Returns a v2 response. */
  onExpand: (seed: string, scenes: SceneType[], countPerScene: number) => Promise<ExpandQueriesResp>;
  /** The user checked a candidate query in some scene tab; the parent handles persisting to DB */
  onPickCandidate: (text: string, scene: SceneType, seed: string) => void;
  /** Checked set (to echo checked state on the candidate list) */
  selectedSet: Set<string>;
  /** readonly passed through from the outer layer after expansion, to prevent edits once submitted for review */
  readOnly?: boolean;
  /** Default number of items per scene, default 30 (backend cap; from 2026-09-21, previously 50 -- 50 made all intent/brand scenes time out) */
  defaultCountPerScene?: number;
  /** Product list from the profile + registration callback (2026-09-22): when expansion hits no product, offer "mark as this brand's product" next to the seed */
  products?: BrandProduct[];
  onAddProduct?: (p: BrandProduct) => void | Promise<void>;
}

// Output of the current expansion -- grouped by scene
interface RunState {
  seed: string;
  results: Partial<Record<SceneType, { queries: string[]; error?: string | null }>>;
  // Which product in the profile the seed matched (2026-09-22; backend find_product). null = expand as a regular category seed
  matchedProduct?: ExpandQueriesResp['matched_product'];
}

export function SceneExpander({
  brandSceneAvailable,
  onExpand,
  onPickCandidate,
  selectedSet,
  readOnly = false,
  defaultCountPerScene = 30,
  products,
  onAddProduct,
}: Props) {
  const { t } = useTranslation();
  const [seed, setSeed] = useState('');
  const [enabledScenes, setEnabledScenes] = useState<Set<SceneType>>(
    () => new Set<SceneType>(ALL_SCENES.filter(s => s !== 'brand' || brandSceneAvailable)),
  );
  const [countPerScene, setCountPerScene] = useState(defaultCountPerScene);
  const [run, setRun] = useState<RunState | null>(null);
  const [activeTab, setActiveTab] = useState<SceneType>('search');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggleScene = (s: SceneType) => {
    if (s === 'brand' && !brandSceneAvailable) return;
    const next = new Set(enabledScenes);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setEnabledScenes(next);
  };

  const handleExpand = async () => {
    const seedTrim = seed.trim();
    if (!seedTrim || loading) return;
    if (enabledScenes.size === 0) {
      setErr(t('topic.profile.monitor.scene.pickAtLeastOne'));
      return;
    }
    setLoading(true); setErr(null);
    try {
      const resp = await onExpand(seedTrim, Array.from(enabledScenes), countPerScene);
      const sceneMap = resp.scenes || {};
      // Old schema fallback: if there's only a .queries array and no .scenes, treat it as the search scene
      const compatScenes: RunState['results'] = {};
      if (Object.keys(sceneMap).length === 0 && Array.isArray(resp.queries)) {
        compatScenes.search = { queries: resp.queries };
      } else {
        for (const s of ALL_SCENES) {
          const v = sceneMap[s];
          if (v) compatScenes[s] = { queries: v.queries || [], error: v.error || null };
        }
      }
      setRun({ seed: seedTrim, results: compatScenes, matchedProduct: resp.matched_product ?? null });
      // Automatically switch to the first tab with output
      const firstNonEmpty = ALL_SCENES.find(s => (compatScenes[s]?.queries.length || 0) > 0);
      if (firstNonEmpty) setActiveTab(firstNonEmpty);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-md p-3 space-y-3"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <p className="text-sm font-semibold text-primary">
        {t('topic.profile.monitor.scene.title')}
      </p>
      <p className="text-xs text-muted">
        {t('topic.profile.monitor.scene.hint')}
      </p>

      {!readOnly && (
        <>
          <div className="flex gap-2 flex-wrap items-center">
            <input type="text" value={seed} onChange={e => setSeed(e.target.value)}
                   placeholder={t('topic.profile.monitor.seedPlaceholder')}
                   className="flex-1 min-w-[180px] text-sm px-3 py-2 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <input type="number" min={5} max={30} value={countPerScene}
                   onChange={e => setCountPerScene(Math.min(30, Math.max(5, +e.target.value || 30)))}
                   title={t('topic.profile.monitor.scene.countPerSceneHint')}
                   className="w-20 text-sm px-3 py-2 rounded-md"
                   style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }} />
            <button type="button" onClick={handleExpand} disabled={loading || !seed.trim() || enabledScenes.size === 0}
                    className="text-sm px-4 py-2 rounded-md text-white"
                    style={{ background: 'var(--accent-primary)',
                             opacity: (loading || !seed.trim() || enabledScenes.size === 0) ? 0.5 : 1 }}>
              {loading ? t('topic.profile.monitor.scene.expanding') : t('topic.profile.monitor.scene.expand')}
            </button>
          </div>

          <div className="flex gap-3 flex-wrap items-center text-xs">
            <span className="text-muted">{t('topic.profile.monitor.scene.dimensions')}</span>
            {ALL_SCENES.map(s => {
              const disabled = s === 'brand' && !brandSceneAvailable;
              const checked = enabledScenes.has(s) && !disabled;
              return (
                <label key={s}
                       className="inline-flex items-center gap-1.5 cursor-pointer"
                       style={{ opacity: disabled ? 0.4 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}
                       title={disabled ? t('topic.profile.monitor.scene.brand.disabledHint') : ''}>
                  <input type="checkbox" checked={checked} disabled={disabled}
                         onChange={() => toggleScene(s)}
                         style={{ accentColor: 'var(--accent-primary)' }} />
                  <span className="text-primary">
                    {t(`topic.profile.monitor.scene.${s}.label`)}
                  </span>
                </label>
              );
            })}
          </div>
        </>
      )}

      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

      {run && !run.matchedProduct && onAddProduct && (
        <MarkSeedAsProduct seed={run.seed} products={products} onAdd={onAddProduct} disabled={readOnly} />
      )}
      {run?.matchedProduct && (
        <div className="text-xs rounded-md px-3 py-2"
             style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
          {t('topic.profile.monitor.scene.matchedProduct', {
            name: run.matchedProduct.name,
            category: run.matchedProduct.category || t('topic.profile.monitor.scene.matchedProductNoCategory'),
          })}
        </div>
      )}

      {run && (
        <div className="space-y-2">
          <div className="text-xs text-muted">
            {t('topic.profile.monitor.scene.resultFor', { seed: run.seed })}
          </div>
          <div className="flex gap-1 flex-wrap border-b" style={{ borderColor: 'var(--border-color)' }}>
            {ALL_SCENES.map(s => {
              const r = run.results[s];
              const count = r?.queries.length || 0;
              const hasErr = !!r?.error;
              const isActive = s === activeTab;
              const isAvailable = !!r; // this scene ran this time (may be count=0 + error)
              return (
                <button key={s}
                        onClick={() => isAvailable && setActiveTab(s)}
                        disabled={!isAvailable}
                        className="text-xs px-3 py-1.5 rounded-t-md"
                        style={{
                          background: isActive ? 'var(--bg-tertiary)' : 'transparent',
                          color: hasErr ? 'var(--danger-fg)' : isActive ? 'var(--accent-primary)' : 'var(--text-secondary)',
                          borderBottom: isActive ? '2px solid var(--accent-primary)' : 'none',
                          opacity: isAvailable ? 1 : 0.4,
                          cursor: isAvailable ? 'pointer' : 'not-allowed',
                        }}>
                  {t(`topic.profile.monitor.scene.${s}.label`)}
                  <span className="ml-1.5 inline-block px-1.5 py-0.5 rounded-full text-[10px]"
                        style={{
                          background: isActive ? 'var(--accent-primary)' : 'var(--bg-card)',
                          color: isActive ? '#fff' : 'var(--text-muted)',
                        }}>
                    {hasErr ? '!' : count}
                  </span>
                </button>
              );
            })}
          </div>

          <SceneCandidateList
            scene={activeTab}
            seed={run.seed}
            data={run.results[activeTab]}
            selectedSet={selectedSet}
            onPick={onPickCandidate}
            readOnly={readOnly}
          />
        </div>
      )}
    </div>
  );
}

function SceneCandidateList({
  scene, seed, data, selectedSet, onPick, readOnly,
}: {
  scene: SceneType;
  seed: string;
  data: { queries: string[]; error?: string | null } | undefined;
  selectedSet: Set<string>;
  onPick: (text: string, scene: SceneType, seed: string) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  if (!data) {
    return (
      <div className="text-xs text-muted py-4 text-center">
        {t('topic.profile.monitor.scene.notRun')}
      </div>
    );
  }
  if (data.error) {
    return (
      <div className="text-xs py-4 px-3 rounded-md"
           style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
        {data.error}
      </div>
    );
  }
  if (data.queries.length === 0) {
    return (
      <div className="text-xs text-muted py-4 text-center">
        {t('topic.profile.monitor.scene.empty')}
      </div>
    );
  }
  return (
    <div className="space-y-1.5 max-h-[360px] overflow-y-auto pr-1">
      {data.queries.map(q => {
        const checked = selectedSet.has(q);
        return (
          <label key={q}
                 className="flex items-start gap-2 p-2 rounded-md cursor-pointer"
                 style={{
                   background: checked ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                   border: `1px solid ${checked ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                   opacity: readOnly ? 0.6 : 1,
                 }}>
            <input type="checkbox" checked={checked} disabled={readOnly}
                   onChange={() => onPick(q, scene, seed)}
                   className="mt-0.5"
                   style={{ accentColor: 'var(--accent-primary)' }} />
            <span className="text-sm text-primary flex-1">{q}</span>
          </label>
        );
      })}
    </div>
  );
}
