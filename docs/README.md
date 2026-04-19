# docs/ 索引

项目文档目录。**权威文档**标 ⭐;**历史文档**保留但不再维护。

## 部署与运维

| 文档 | 说明 |
|---|---|
| ⭐ [deployment-guide.md](./deployment-guide.md) | 线上真实部署指南(www.vigilath.com),含 systemd / nginx / 发布流程 / 回滚 |
| [部署文档.md](./部署文档.md) | 早期部署文档,被 `deployment-guide.md` 取代,仅作历史参考 |

## 架构与规范

| 文档 | 说明 |
|---|---|
| [url-validation-cases.md](./url-validation-cases.md) | 前后端 URL 校验规则的单一事实源,含 22 条 case |
| [i18n-status.md](./i18n-status.md) | 前端中英双语覆盖现状与未完成项 |
| [系统处理方案.md](./系统处理方案.md) | 系统级功能实现方案 |

## 性能

| 文档 | 说明 |
|---|---|
| [performance-guide.md](./performance-guide.md) | 性能诊断与优化手册 |
| [性能处理方案.md](./性能处理方案.md) | 性能改造方案 |
| [performance-report-2026-04-16.md](./performance-report-2026-04-16.md) | 性能快照(2026-04-16) |
| [performance-report-2026-04-17.md](./performance-report-2026-04-17.md) | 性能快照(2026-04-17) |

## 需求与规划

| 文档 | 说明 |
|---|---|
| [需求文档.md](./需求文档.md) | 初版功能整理 |
| [需求文档-付费解锁.md](./需求文档-付费解锁.md) | 付费解锁与权限控制需求 |
| [需求文档-前端检测体验重构.md](./需求文档-前端检测体验重构.md) | 首页 + 结果页 + 会员分级重构需求 |
| [用户权益处理方案.md](./用户权益处理方案.md) | 用户权益方案 |
| [会员功能免费与付费功能项目列表.md](./会员功能免费与付费功能项目列表.md) | 会员免费 / 付费功能清单 |
| [ssg-home-plan.md](./ssg-home-plan.md) | 首页 SSG 预渲染技术方案 |

## 支付集成

| 文档 | 说明 |
|---|---|
| [moltspay-integration-plan.md](./moltspay-integration-plan.md) | MoltsPay 支付集成开发计划 |
| [moltspay-x402-browser-integration.md](./moltspay-x402-browser-integration.md) | MoltsPay x402 浏览器集成方案 |

## 商业化与品牌

| 文档 | 说明 |
|---|---|
| [品牌定位升级方案.md](./品牌定位升级方案.md) | GApex 品牌定位升级 |
| [商业化增长方案.md](./商业化增长方案.md) | GApex 商业化增长方案 |
| [前端视觉重构方案.md](./前端视觉重构方案.md) | 前端视觉方向 A(深色霓虹赛博) |

## 分析与杂项

| 文档 | 说明 |
|---|---|
| [ai-cost-analysis.md](./ai-cost-analysis.md) | AI 调用成本分析 |
| [self-geo-optimization.md](./self-geo-optimization.md) | GApex 自身 GEO 优化清单 |
| [user-guide.md](./user-guide.md) | 面向用户的使用指南 |
| [issue_list.md](./issue_list.md) | 问题 / 缺陷清单 |

---

**维护约定:**

- 新增文档时,在对应分组追加一行;若无合适分组则新开分组,不要把索引塞成平铺列表
- 废弃文档不删,打 "历史" 标记并指向替代文档
- 权威文档 ⭐ 标记仅用于同主题有多份时的消歧,避免读者看错版本
