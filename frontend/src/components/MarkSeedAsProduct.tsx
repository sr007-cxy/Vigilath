// "This seed is a product of this brand" -- one click next to the seed registers it into Section 3 of the profile (2026-09-22).
//
// The backend only uses product mode for query expansion if the product is registered in the profile's products (building category terms by category, classifying product evaluation questions as
// brand terms); an unregistered 「<brand-e>手柄」 ("<brand-e> controller") would be treated as a category, expanding into terms like 「<brand-e>手柄哪家好」 ("which <brand-e> controller is best") that treat your own product as another
// brand. This lets users register it right where they add seeds, without going to the profile page.
//
// Matching rules in utils/products.findProduct (same metric definition as backend find_product, used only to decide whether to show the hint).

import { useState } from 'react';
import type { BrandProduct } from '../services/aiTelemetryApi';
import { EMPTY_BRAND_PRODUCT } from '../services/aiTelemetryApi';
import { findProduct } from '../utils/products';

interface Props {
  seed: string;
  products: BrandProduct[] | undefined;
  onAdd: (p: BrandProduct) => void | Promise<void>;
  disabled?: boolean;
}

export function MarkSeedAsProduct({ seed, products, onAdd, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(seed);
  const [category, setCategory] = useState('');
  const [busy, setBusy] = useState(false);
  const matched = findProduct(products, seed);
  if (matched) {
    return (
      <span className="text-xs" style={{ color: 'var(--accent-primary)' }}>
        ✓ 已登记为本品牌产品「{matched.name}」{matched.category ? `(品类:${matched.category})` : '(未填品类,扩词时由 AI 推断)'}
      </span>
    );
  }
  if (!open) {
    return (
      <button type="button" disabled={disabled} onClick={() => { setName(seed); setOpen(true); }}
              className="text-xs px-2 py-0.5 rounded-md"
              style={{ color: 'var(--text-secondary)', border: '1px dashed var(--border-color)' }}
              title="登记后:搜索 / 问答 / 意图词按它的品类扩,产品评估问句归品牌词;不登记会被当成品类,扩出「XX哪家好」这种把自家产品当别家品牌的词">
        「{seed}」是本品牌产品?标记
      </button>
    );
  }
  const inputStyle = { background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' } as const;
  const submit = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      await onAdd({ ...EMPTY_BRAND_PRODUCT, name: n, category: category.trim() });
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="产品 / 型号名"
             className="px-2 py-0.5 rounded-md w-36" style={inputStyle} />
      <input type="text" value={category} onChange={e => setCategory(e.target.value)} placeholder="品类通用名(如 游戏手柄)"
             onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void submit(); } }}
             className="px-2 py-0.5 rounded-md w-44" style={inputStyle} />
      <button type="button" disabled={busy || !name.trim()} onClick={() => void submit()}
              className="px-2 py-0.5 rounded-md disabled:opacity-50" style={{ background: 'var(--accent-primary)', color: '#fff' }}>
        {busy ? '登记中…' : '登记为产品'}
      </button>
      <button type="button" onClick={() => setOpen(false)} className="px-2 py-0.5 text-muted">取消</button>
      <span className="text-muted">品类填通用名,扩词按它造品类词;卖点 / 人群可以之后在资料第三节补</span>
    </div>
  );
}
