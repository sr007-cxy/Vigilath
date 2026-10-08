// Data for the GEO service catalog table shown on the CN site (REGION=cn) "Products & services" page.
// The global site uses membership plans + a price comparison table, not this data.
// Structure: service module -> service item -> service details (description / core value / optional quantity note).

export interface ServiceLeaf {
  /** Service details */
  detail: string;
  /** Description */
  desc: string;
  /** Core value */
  value: string;
  /** Quantity / note (e.g. "≥ 300 篇"), optional */
  note?: string;
}

export interface ServiceItem {
  /** Service item */
  item: string;
  leaves: ServiceLeaf[];
}

export interface ServiceModule {
  /** Service module */
  module: string;
  items: ServiceItem[];
}

export const CN_SERVICE_MODULES: ServiceModule[] = [
  {
    module: '官网 GEO 架构深度优化',
    items: [
      {
        item: '网站层级重构',
        leaves: [
          {
            detail: '蚕茧式目录规划',
            desc: '按产品 / 解决方案 / 技术 / 资质四大维度,搭建层级化官网目录,破除扁平化结构,实现权重精准汇聚',
            value: '让 AI / 搜索引擎清晰识别核心业务,避免权重分散',
          },
          {
            detail: '页面角色定位',
            desc: '为产品页、解决方案页、技术页、博客页、资质页定义专属业务角色,明确页面核心使命',
            value: '强化页面语义属性,提升 AI 抓取精准度',
          },
        ],
      },
      {
        item: '结构化数据部署',
        leaves: [
          {
            detail: 'Schema 标准标记',
            desc: '部署 Organization / Product / FAQPage / Article 四大类 JSON-LD 结构化数据',
            value: '为 AI 提供机器可读官方定义,提升信源优先级',
          },
          {
            detail: '爬虫适配优化',
            desc: '优化动态加载内容,提升爬虫抓取完整性,消除 AI 识别盲区',
            value: '解决内容模糊化问题,确保 AI 全量读取信息',
          },
        ],
      },
      {
        item: '导航体验优化',
        leaves: [
          {
            detail: '面包屑导航部署',
            desc: '全页面配置层级化面包屑,明确页面父子级关系',
            value: '构建清晰知识路径,强化 AI 页面层级认知',
          },
          {
            detail: '语义页脚规划',
            desc: '按技术 / 硬件 / 场景 / 资质分类布局页脚链接,强化语义关联',
            value: '提升全站权重流通效率,完善 AI 知识图谱',
          },
        ],
      },
      {
        item: 'GEO 优化分析报告',
        leaves: [
          {
            detail: '解锁无限次专业 GEO & AEO 工具',
            desc: 'AEO 可见性审计、竞争对比检测、AI 爬虫测试、权威信号审计、AI 引用检测、AI 可见性审计、实体 GEO 审计,综合报告等',
            value: '整合优化、内容、检测、对标全流程服务,全方位筑牢品牌在 AI 平台的流量与竞争优势',
          },
        ],
      },
    ],
  },
  {
    module: 'AI 权威信号搭建',
    items: [
      {
        item: '关键词分层体系',
        leaves: [
          {
            detail: '转化词精准布局',
            desc: '筛选产品、价格、合作等高转化意图关键词,精准优化',
            value: '直接拉动业务转化,抢占核心流量',
          },
          {
            detail: '场景词 / 信息词全覆盖',
            desc: '布局场景需求、科普信息类关键词,构建全链路词库',
            value: '覆盖用户全决策周期,源源不断引入流量',
          },
        ],
      },
      {
        item: 'AI 问答数据源打造',
        leaves: [
          {
            detail: '标准问答库搭建',
            desc: '针对 AI 高频提问,打造标准化、结构化回答内容',
            value: '成为 AI 生成答案的首选数据源',
          },
          {
            detail: '语义关联标注',
            desc: '对产品、技术、场景、资质做语义关联标注',
            value: '完善 AI 知识图谱,实现精准推荐',
          },
        ],
      },
      {
        item: '高质量垂直场景页建设',
        leaves: [
          {
            detail: 'pGEO 站点搭建',
            desc: '基于人群画像 + 场景 + 解决方案,批量生成专属长尾落地页',
            value: '每月持续优化和更新,低成本覆盖海量精准长尾需求',
          },
          {
            detail: '长尾关键词内容拓展覆盖',
            desc: '布局低竞争、高价值长尾词,实现全场景流量截流',
            value: '拓宽流量入口,提升全域曝光',
          },
        ],
      },
      {
        item: '垂直媒体传播',
        leaves: [
          {
            detail: '品牌重磅新闻投放',
            desc: '发布品牌技术突破、产品发布、战略布局等核心新闻,覆盖国内头部科技媒体',
            value: '树立硬科技初创标杆,AI 认定行业事实',
            note: '≥ 300 篇',
          },
          {
            detail: '行业深度观点传播',
            desc: '发布行业趋势、技术创新方向等深度观点内容,打造品牌行业领导力',
            value: '提升品牌行业格局与专业影响力,强化 AI 检索中的权威标签',
          },
          {
            detail: '核心技术成果曝光',
            desc: '投放品牌核心技术、产品创新等专业内容,获取科技行业认可',
            value: '获得科技行业权威背书,提升品牌技术可信度',
          },
        ],
      },
      {
        item: '公共媒体库',
        leaves: [
          {
            detail: '垂直领域内容分发',
            desc: '面向品牌所属垂直行业,发布场景应用、解决方案、行业合规相关内容',
            value: '强化垂直领域专业公信力,精准触达行业目标客户',
          },
          {
            detail: '行业图谱与资讯露出',
            desc: '加入垂直行业权威图谱,发布行业技术与产业动态内容',
            value: '成为 AI 检索垂直领域头部品牌的核心参考来源',
          },
        ],
      },
      {
        item: '权威媒体库',
        leaves: [
          {
            detail: '商业品牌专栏塑造',
            desc: '发布行业商业前景、品牌商业价值等专栏内容',
            value: '打造高端商业品牌形象,提升行业商业公信力',
          },
          {
            detail: '科技商业前景专栏',
            desc: '发布科技商业前景、产业趋势等高端专栏内容',
            value: '打造高端商业形象,提升行业公信力',
          },
        ],
      },
    ],
  },
];
