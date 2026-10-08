// CN site "Products & services" service catalog table (merged cells). Rendered only when REGION=cn.
// Data from ../data/cnServiceModules. The global site doesn't mount this component.
import { CN_SERVICE_MODULES } from '../data/cnServiceModules';

const TH = 'px-4 py-3.5 text-left text-xs sm:text-sm font-semibold text-secondary whitespace-nowrap';

export function CnServiceModules() {
  return (
    <div className="overflow-x-auto scrollbar-hide -mx-4 px-4 sm:mx-0 sm:px-0">
      <div
        className="rounded-2xl border overflow-hidden"
        style={{ borderColor: 'var(--border-color)', background: 'var(--bg-card)' }}
      >
        <table className="w-full border-collapse min-w-[860px] text-sm">
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.03)', borderBottom: '1px solid var(--border-color)' }}>
              <th className={TH}>服务模块</th>
              <th className={TH}>服务项</th>
              <th className={TH}>服务明细</th>
              <th className={TH}>描述</th>
              <th className={TH}>核心价值</th>
            </tr>
          </thead>
          <tbody>
            {CN_SERVICE_MODULES.map((mod, mi) => {
              const moduleRows = mod.items.reduce((s, it) => s + it.leaves.length, 0);
              return mod.items.map((it, ii) =>
                it.leaves.map((leaf, li) => {
                  const firstOfModule = ii === 0 && li === 0;
                  const firstOfItem = li === 0;
                  return (
                    <tr
                      key={`${mi}-${ii}-${li}`}
                      className="transition-colors hover:bg-white/[0.02]"
                      style={{ borderTop: '1px solid var(--border-color)' }}
                    >
                      {firstOfModule && (
                        <td
                          rowSpan={moduleRows}
                          className="px-4 py-3 align-middle text-center font-bold text-base"
                          style={{
                            background:
                              mi % 2 === 0
                                ? 'rgba(99,102,241,0.07)'
                                : 'rgba(16,185,129,0.07)',
                            borderRight: '1px solid var(--border-color)',
                          }}
                        >
                          <span className="gradient-text">{mod.module}</span>
                        </td>
                      )}
                      {firstOfItem && (
                        <td
                          rowSpan={it.leaves.length}
                          className="px-4 py-3 align-top font-semibold text-primary whitespace-nowrap"
                          style={{ borderRight: '1px solid var(--border-color)' }}
                        >
                          {it.item}
                        </td>
                      )}
                      <td className="px-4 py-3 align-top font-medium text-primary min-w-[160px]">
                        <div className="flex items-start gap-2">
                          <span>{leaf.detail}</span>
                          {leaf.note && (
                            <span
                              className="shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                              style={{ background: 'rgba(99,102,241,0.15)', color: 'var(--accent-primary)' }}
                            >
                              {leaf.note}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top text-secondary leading-relaxed max-w-[320px]">
                        {leaf.desc}
                      </td>
                      <td className="px-4 py-3 align-top text-primary leading-relaxed max-w-[280px]">
                        <div className="flex items-start gap-2">
                          <svg
                            className="w-4 h-4 shrink-0 mt-0.5"
                            style={{ color: 'var(--accent-primary)' }}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" />
                          </svg>
                          <span>{leaf.value}</span>
                        </div>
                      </td>
                    </tr>
                  );
                }),
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
