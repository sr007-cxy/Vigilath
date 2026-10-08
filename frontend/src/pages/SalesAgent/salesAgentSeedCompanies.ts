// Sales Agent MVP hardcoded company seed library.
// This variable is the runtime data source; the original JSON has been merged into this file.
// Constraint: only company-level public mailboxes; without a real audit report, no specific GEO/SEO/AI Visibility issues may be generated.

export type SalesAgentSeedContact = {
  contact_type: 'email';
  contact_value: string;
  source_url: string;
  is_public_company_contact: true;
  confidence: 'high' | 'medium' | 'low' | string;
};

export type SalesAgentSeedCompany = {
  canonical_name: string;
  website_url: string;
  industry_primary: string;
  industry_secondary: string;
  province: string;
  city: string | null;
  emails: string[];
  contacts: SalesAgentSeedContact[];
  source_url: string;
  confidence: 'high' | 'medium' | 'low' | string;
  missing_fields: string[];
  collected_at: string;
};

export const SALES_AGENT_SEED_COMPANIES: SalesAgentSeedCompany[] = [
  {
    "canonical_name": "示例企业001有限公司",
    "website_url": "https://www.company001.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "跨境电商 / OEM / ODM",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "info@company001.example.com",
      "sales@company001.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company001.example.com",
        "source_url": "https://www.company001.example.com/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "sales@company001.example.com",
        "source_url": "https://www.company001.example.com/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company001.example.com/contact-us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业002有限公司",
    "website_url": "https://www.company002.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "连接器 / 电子组件",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company002.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company002.example.com",
        "source_url": "https://www.company002.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company002.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业003有限公司",
    "website_url": "https://www.company003.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "电路板 / PCB",
    "province": "广东省",
    "city": "广州市",
    "emails": [
      "info@company003.example.com",
      "sales@company003.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company003.example.com",
        "source_url": "https://www.company003.example.com/zh/contact.php",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "sales@company003.example.com",
        "source_url": "https://www.company003.example.com/zh/contact.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company003.example.com/zh/contact.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业004有限公司",
    "website_url": "https://www.company004.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "工控主板 / 嵌入式",
    "province": "广东省",
    "city": "广州市",
    "emails": [
      "sales@company004.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company004.example.com",
        "source_url": "https://www.company004.example.com/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company004.example.com/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业005有限公司",
    "website_url": "https://www.company005.example.com/",
    "industry_primary": "PCB制造",
    "industry_secondary": "电子制造",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "info@company005.example.com",
      "sales@company005.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company005.example.com",
        "source_url": "https://www.company005.example.com/Cn/SinglePage.aspx",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "sales@company005.example.com",
        "source_url": "https://www.company005.example.com/Cn/SinglePage.aspx",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company005.example.com/Cn/SinglePage.aspx",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业006有限公司",
    "website_url": "https://www.company006.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "人工智能 / 物联网",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company006.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company006.example.com",
        "source_url": "https://www.company006.example.com/lxwm.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company006.example.com/lxwm.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业007有限公司",
    "website_url": "https://www.company007.example.com/",
    "industry_primary": "工业电源",
    "industry_secondary": "电力电子 / 智能制造",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company007.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company007.example.com",
        "source_url": "https://www.company007.example.com/contact/index.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company007.example.com/contact/index.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业008有限公司",
    "website_url": "https://www.company008.example.com/",
    "industry_primary": "电子设备制造",
    "industry_secondary": "条码打印 / 自动识别",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company008.example.com",
      "marketing@company008.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company008.example.com",
        "source_url": "https://www.company008.example.com/contact_us/we/",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "marketing@company008.example.com",
        "source_url": "https://www.company008.example.com/contact_us/we/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company008.example.com/contact_us/we/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业009有限公司",
    "website_url": "https://www.company009.example.com/",
    "industry_primary": "半导体",
    "industry_secondary": "电子元器件 / 芯片设计",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company009.example.com",
      "marketing@company009.example.com",
      "support@company009.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company009.example.com",
        "source_url": "https://www.company009.example.com/about-us/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "marketing@company009.example.com",
        "source_url": "https://www.company009.example.com/about-us/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "support@company009.example.com",
        "source_url": "https://www.company009.example.com/about-us/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company009.example.com/about-us/contact-us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业010有限公司",
    "website_url": "https://www.company010.example.com/",
    "industry_primary": "电子设备制造",
    "industry_secondary": "会议系统 / 音视频",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company010.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company010.example.com",
        "source_url": "https://www.company010.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company010.example.com/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业011有限公司",
    "website_url": "https://www.company011.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "物联网设备",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company011.example.com",
      "info@company011.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company011.example.com",
        "source_url": "https://www.company011.example.com/lists/35.html",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "info@company011.example.com",
        "source_url": "https://www.company011.example.com/lists/35.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company011.example.com/lists/35.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业012有限公司",
    "website_url": "https://www.company012.example.com/",
    "industry_primary": "软件服务",
    "industry_secondary": "企业营销 / 数据服务",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company012.example.com",
      "marketing@company012.example.com",
      "support@company012.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company012.example.com",
        "source_url": "https://www.company012.example.com/about",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "marketing@company012.example.com",
        "source_url": "https://www.company012.example.com/about",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "support@company012.example.com",
        "source_url": "https://www.company012.example.com/about",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company012.example.com/about",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业013有限公司",
    "website_url": "https://www.company013.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "无人机 / 航模",
    "province": "广东省",
    "city": "广州市",
    "emails": [
      "contact@company013.example.com",
      "contact@company013.example.com",
      "marketing@company013.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company013.example.com",
        "source_url": "https://www.company013.example.com/solution/index.php",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "contact@company013.example.com",
        "source_url": "https://www.company013.example.com/solution/index.php",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "marketing@company013.example.com",
        "source_url": "https://www.company013.example.com/solution/index.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company013.example.com/solution/index.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业014有限公司",
    "website_url": "https://www.company014.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "计算机硬件 / 外贸制造",
    "province": "广东省",
    "city": "广州市",
    "emails": [
      "sales@company014.example.com",
      "info@company014.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company014.example.com",
        "source_url": "https://www.company014.example.com/contact_us/",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "info@company014.example.com",
        "source_url": "https://www.company014.example.com/contact_us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company014.example.com/contact_us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业015有限公司",
    "website_url": "https://www.company015.example.com/",
    "industry_primary": "装备制造",
    "industry_secondary": "压缩机 / 工业设备",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "sales@company015.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company015.example.com",
        "source_url": "https://www.company015.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company015.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业016有限公司",
    "website_url": "https://www.company016.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "连接器 / 工厂配套",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "sales@company016.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company016.example.com",
        "source_url": "https://www.company016.example.com/Contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company016.example.com/Contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业017有限公司",
    "website_url": "https://www.company017.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "电源设备",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "sales@company017.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company017.example.com",
        "source_url": "https://www.company017.example.com/contactus/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company017.example.com/contactus/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业018有限公司",
    "website_url": "https://www.company018.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "智能硬件 / 外贸制造",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "market@company018.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "market@company018.example.com",
        "source_url": "https://www.company018.example.com/en/contactus/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company018.example.com/en/contactus/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业019有限公司",
    "website_url": "https://www.company019.example.com/",
    "industry_primary": "智能硬件",
    "industry_secondary": "电子制造",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "sales@company019.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company019.example.com",
        "source_url": "https://www.company019.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company019.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业020有限公司",
    "website_url": "https://www.company020.example.com/",
    "industry_primary": "物联网",
    "industry_secondary": "RFID / 智能硬件",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "sales@company020.example.com",
      "contact@company020.example.com",
      "contact@company020.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company020.example.com",
        "source_url": "https://www.company020.example.com/home/Lists/index/tid/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "contact@company020.example.com",
        "source_url": "https://www.company020.example.com/home/Lists/index/tid/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      },
      {
        "contact_type": "email",
        "contact_value": "contact@company020.example.com",
        "source_url": "https://www.company020.example.com/home/Lists/index/tid/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company020.example.com/home/Lists/index/tid/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业021有限公司",
    "website_url": "https://www.company021.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "工程机械",
    "province": "山东省",
    "city": "济宁市",
    "emails": [
      "info@company021.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company021.example.com",
        "source_url": "https://www.company021.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company021.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业022有限公司",
    "website_url": "https://www.company022.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "铸造 / 铸件",
    "province": "山东省",
    "city": "菏泽市",
    "emails": [
      "sales@company022.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company022.example.com",
        "source_url": "https://www.company022.example.com/honor_1/1.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company022.example.com/honor_1/1.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业023有限公司",
    "website_url": "https://www.company023.example.com/",
    "industry_primary": "化工",
    "industry_secondary": "精细化工",
    "province": "山东省",
    "city": null,
    "emails": [
      "sales@company023.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company023.example.com",
        "source_url": "https://www.company023.example.com/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company023.example.com/contact-us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业024有限公司",
    "website_url": "https://www.company024.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "材料科技",
    "province": "山东省",
    "city": null,
    "emails": [
      "sales@company024.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company024.example.com",
        "source_url": "https://www.company024.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company024.example.com/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业025有限公司",
    "website_url": "https://www.company025.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "工业机械",
    "province": "山东省",
    "city": null,
    "emails": [
      "sales@company025.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company025.example.com",
        "source_url": "https://www.company025.example.com/about_contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company025.example.com/about_contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业026有限公司",
    "website_url": "https://www.company026.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "塑料机械",
    "province": "山东省",
    "city": "潍坊市",
    "emails": [
      "sales@company026.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company026.example.com",
        "source_url": "https://www.company026.example.com/about.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company026.example.com/about.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业027有限公司",
    "website_url": "https://www.company027.example.com/",
    "industry_primary": "化工",
    "industry_secondary": "精细化工",
    "province": "山东省",
    "city": null,
    "emails": [
      "sales@company027.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company027.example.com",
        "source_url": "https://www.company027.example.com/a/contact/lianxifangshi/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company027.example.com/a/contact/lianxifangshi/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业028有限公司",
    "website_url": "https://www.company028.example.com/",
    "industry_primary": "化工",
    "industry_secondary": "化工材料",
    "province": "安徽省",
    "city": "合肥市",
    "emails": [
      "sales@company028.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company028.example.com",
        "source_url": "https://www.company028.example.com/lianxiwomen/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company028.example.com/lianxiwomen/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业029有限公司",
    "website_url": "https://www.company029.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "船舶五金 / 锚链",
    "province": "安徽省",
    "city": null,
    "emails": [
      "sales@company029.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company029.example.com",
        "source_url": "https://www.company029.example.com/index.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company029.example.com/index.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业030有限公司",
    "website_url": "https://www.company030.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "工业物联网",
    "province": "湖北省",
    "city": "武汉市",
    "emails": [
      "sales@company030.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company030.example.com",
        "source_url": "https://www.company030.example.com/index/index/contactdata.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company030.example.com/index/index/contactdata.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业031有限公司",
    "website_url": "https://www.company031.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "电气设备",
    "province": "湖北省",
    "city": null,
    "emails": [
      "sales@company031.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company031.example.com",
        "source_url": "https://www.company031.example.com/cn/contactus/index.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company031.example.com/cn/contactus/index.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业032有限公司",
    "website_url": "https://www.company032.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "电力线缆",
    "province": "湖南省",
    "city": "湘潭市",
    "emails": [
      "sales@company032.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company032.example.com",
        "source_url": "https://www.company032.example.com/lxwm.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company032.example.com/lxwm.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业033有限公司",
    "website_url": "https://www.company033.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "机器人",
    "province": "湖南省",
    "city": null,
    "emails": [
      "sales@company033.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company033.example.com",
        "source_url": "https://www.company033.example.com/connect/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company033.example.com/connect/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业034有限公司",
    "website_url": "https://www.company034.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "膜材料 / 新材料",
    "province": "湖南省",
    "city": null,
    "emails": [
      "sales@company034.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company034.example.com",
        "source_url": "https://www.company034.example.com/lxwm.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company034.example.com/lxwm.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业035有限公司",
    "website_url": "https://www.company035.example.com/",
    "industry_primary": "化工",
    "industry_secondary": "精细化工",
    "province": "四川省",
    "city": null,
    "emails": [
      "sales@company035.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company035.example.com",
        "source_url": "https://www.company035.example.com/contact_us.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company035.example.com/contact_us.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业036有限公司",
    "website_url": "https://www.company036.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "造纸机械",
    "province": "四川省",
    "city": "成都市",
    "emails": [
      "sales@company036.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company036.example.com",
        "source_url": "https://www.company036.example.com/Contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company036.example.com/Contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业037有限公司",
    "website_url": "https://www.company037.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "光电子",
    "province": "福建省",
    "city": "福州市",
    "emails": [
      "sales@company037.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company037.example.com",
        "source_url": "https://www.company037.example.com/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company037.example.com/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业038有限公司",
    "website_url": "https://www.company038.example.com/",
    "industry_primary": "生物科技",
    "industry_secondary": "生物工程",
    "province": "福建省",
    "city": null,
    "emails": [
      "sales@company038.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company038.example.com",
        "source_url": "https://www.company038.example.com/contact.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company038.example.com/contact.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业039有限公司",
    "website_url": "https://www.company039.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "机械制造",
    "province": "福建省",
    "city": "福州市",
    "emails": [
      "sales@company039.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company039.example.com",
        "source_url": "https://www.company039.example.com/ch_in3.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company039.example.com/ch_in3.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业040有限公司",
    "website_url": "https://www.company040.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "机械配件",
    "province": "福建省",
    "city": "泉州市",
    "emails": [
      "sales@company040.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company040.example.com",
        "source_url": "https://www.company040.example.com/about_contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company040.example.com/about_contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业041有限公司",
    "website_url": "https://www.company041.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "SaaS / 数字化营销",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company041.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company041.example.com",
        "source_url": "https://www.company041.example.com/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company041.example.com/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业042有限公司",
    "website_url": "https://www.company042.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "协同办公 / OA SaaS",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "sales@company042.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company042.example.com",
        "source_url": "https://www.company042.example.com/ai/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company042.example.com/ai/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业043有限公司",
    "website_url": "https://www.company043.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "ERP / 财务软件",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company043.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company043.example.com",
        "source_url": "https://www.company043.example.com/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company043.example.com/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业044有限公司",
    "website_url": "https://www.company044.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "房地产数字化 / SaaS",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company044.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company044.example.com",
        "source_url": "https://www.company044.example.com/about/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company044.example.com/about/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业045有限公司",
    "website_url": "https://www.company045.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "协同办公 / 数字化办公",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "marketing@company045.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "marketing@company045.example.com",
        "source_url": "https://www.company045.example.com/Info/constant.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company045.example.com/Info/constant.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业046有限公司",
    "website_url": "https://www.company046.example.com/",
    "industry_primary": "工业软件",
    "industry_secondary": "ERP / 智能制造",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company046.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company046.example.com",
        "source_url": "https://www.company046.example.com/t.php/portal/article/index/id/9955/cid/160.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company046.example.com/t.php/portal/article/index/id/9955/cid/160.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业047有限公司",
    "website_url": "https://www.company047.example.com/zh-cn/",
    "industry_primary": "医疗器械",
    "industry_secondary": "医疗影像设备",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company047.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company047.example.com",
        "source_url": "https://www.company047.example.com/zh-cn/contact-us",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company047.example.com/zh-cn/contact-us",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业048有限公司",
    "website_url": "https://www.company048.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "智能安防 / AIoT",
    "province": "浙江省",
    "city": "杭州市",
    "emails": [
      "support@company048.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "support@company048.example.com",
        "source_url": "https://www.company048.example.com/about/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company048.example.com/about/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业049有限公司",
    "website_url": "https://www.company049.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "锂电装备 / 工业装备",
    "province": "江苏省",
    "city": "无锡市",
    "emails": [
      "contact@company049.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company049.example.com",
        "source_url": "https://www.company049.example.com/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company049.example.com/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业050有限公司",
    "website_url": "https://www.company050.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "工业自动化",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company050.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company050.example.com",
        "source_url": "https://www.company050.example.com/global/support/tech-support.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company050.example.com/global/support/tech-support.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业051有限公司",
    "website_url": "https://www.company051.example.com/",
    "industry_primary": "物流",
    "industry_secondary": "快递 / 物流科技",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company051.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company051.example.com",
        "source_url": "https://www.company051.example.com/html/newPrivacyPolicy2.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company051.example.com/html/newPrivacyPolicy2.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业052有限公司",
    "website_url": "https://www.company052.example.com/cn",
    "industry_primary": "医疗器械",
    "industry_secondary": "医疗设备 / 诊断",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company052.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company052.example.com",
        "source_url": "https://www.company052.example.com/cn/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company052.example.com/cn/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业053有限公司",
    "website_url": "https://www.company053.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "数字化转型服务 / IT咨询",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company053.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company053.example.com",
        "source_url": "https://www.company053.example.com/uploads/document/%E6%B1%89%E5%BE%97%E4%BF%A1%E6%81%AF%EF%BC%9A2024%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A%E5%85%A8%E6%96%87.pdf",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company053.example.com/uploads/document/%E6%B1%89%E5%BE%97%E4%BF%A1%E6%81%AF%EF%BC%9A2024%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A%E5%85%A8%E6%96%87.pdf",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业054有限公司",
    "website_url": "https://www.company054.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "智能安防 / AIoT",
    "province": "浙江省",
    "city": "杭州市",
    "emails": [
      "market@company054.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "market@company054.example.com",
        "source_url": "https://www.company054.example.com/content/dam/hikvision/cn/%E6%8A%95%E8%B5%84%E8%80%85%E5%85%B3%E7%B3%BB/ir%E4%BF%A1%E6%81%AF%E5%BA%93/06%E5%B9%B4%E5%BA%A6%E5%AD%A3%E5%BA%A6%E4%B8%9A%E7%BB%A9%E6%8A%A5%E5%91%8A/2024/%E6%9D%AD%E5%B7%9E%E6%B5%B7%E5%BA%B7%E5%A8%81%E8%A7%86%E6%95%B0%E5%AD%97%E6%8A%80%E6%9C%AF%E8%82%A1%E4%BB%BD%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B82024%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company054.example.com/content/dam/hikvision/cn/%E6%8A%95%E8%B5%84%E8%80%85%E5%85%B3%E7%B3%BB/ir%E4%BF%A1%E6%81%AF%E5%BA%93/06%E5%B9%B4%E5%BA%A6%E5%AD%A3%E5%BA%A6%E4%B8%9A%E7%BB%A9%E6%8A%A5%E5%91%8A/2024/%E6%9D%AD%E5%B7%9E%E6%B5%B7%E5%BA%B7%E5%A8%81%E8%A7%86%E6%95%B0%E5%AD%97%E6%8A%80%E6%9C%AF%E8%82%A1%E4%BB%BD%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B82024%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业055有限公司",
    "website_url": "https://www.company055.example.com/",
    "industry_primary": "工业互联网",
    "industry_secondary": "云计算 / 企业IT",
    "province": "山东省",
    "city": "济南市",
    "emails": [
      "webmaster@company055.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "webmaster@company055.example.com",
        "source_url": "https://www.company055.example.com/lcjtww/gylc32/2346042/index.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company055.example.com/lcjtww/gylc32/2346042/index.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业056有限公司",
    "website_url": "https://www.company056.example.com/",
    "industry_primary": "企业服务",
    "industry_secondary": "通信设备 / ICT",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "support@company056.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "support@company056.example.com",
        "source_url": "https://www.company056.example.com/support/web/Contact.aspx",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company056.example.com/support/web/Contact.aspx",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业057有限公司",
    "website_url": "https://www.company057.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "汽车制造 / 智能网联",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company057.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company057.example.com",
        "source_url": "https://www.company057.example.com/chinese/tzzgx/jbqk/cjwt/index.shtml",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company057.example.com/chinese/tzzgx/jbqk/cjwt/index.shtml",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业058有限公司",
    "website_url": "https://www.company058.example.com/cn/",
    "industry_primary": "智能制造",
    "industry_secondary": "光伏 / 新能源",
    "province": "陕西省",
    "city": "西安市",
    "emails": [
      "market@company058.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "market@company058.example.com",
        "source_url": "https://www.company058.example.com/cn/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company058.example.com/cn/contact-us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业059有限公司",
    "website_url": "https://www.company059.example.com/",
    "industry_primary": "互联网",
    "industry_secondary": "汽车媒体 / 汽车平台",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company059.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company059.example.com",
        "source_url": "https://www.company059.example.com/about/falv.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company059.example.com/about/falv.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业060有限公司",
    "website_url": "https://www.company060.example.com/",
    "industry_primary": "互联网",
    "industry_secondary": "游戏 / 数字娱乐",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company060.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company060.example.com",
        "source_url": "https://www.company060.example.com/investor_relations.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company060.example.com/investor_relations.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业061有限公司",
    "website_url": "https://www.company061.example.com/",
    "industry_primary": "互联网",
    "industry_secondary": "搜索 / AI / 企业服务",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company061.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company061.example.com",
        "source_url": "https://www.company061.example.com/home/index/contact_us",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company061.example.com/home/index/contact_us",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业062有限公司",
    "website_url": "https://www.company062.example.com/",
    "industry_primary": "办公软件",
    "industry_secondary": "企业软件 / 协同办公",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company062.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company062.example.com",
        "source_url": "https://www.company062.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company062.example.com/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业063有限公司",
    "website_url": "https://www.company063.example.com/",
    "industry_primary": "企业软件",
    "industry_secondary": "ERP / 数智化 / 企业AI",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company063.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company063.example.com",
        "source_url": "https://www.company063.example.com/global/privacy/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company063.example.com/global/privacy/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业064有限公司",
    "website_url": "https://www.company064.example.com/",
    "industry_primary": "IT服务",
    "industry_secondary": "软件开发 / 数字化转型",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company064.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company064.example.com",
        "source_url": "https://www.company064.example.com/zh-cn/htmls/shujukufuwuquanzhanjiejuefangan/index.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company064.example.com/zh-cn/htmls/shujukufuwuquanzhanjiejuefangan/index.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业065有限公司",
    "website_url": "https://www.company065.example.com/",
    "industry_primary": "企业软件",
    "industry_secondary": "金融科技 / 系统集成",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company065.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company065.example.com",
        "source_url": "https://www.company065.example.com/list-211-1.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company065.example.com/list-211-1.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业066有限公司",
    "website_url": "https://www.company066.example.com/",
    "industry_primary": "工业科技",
    "industry_secondary": "服务器 / 高性能计算 / 数据中心",
    "province": "天津市",
    "city": "天津市",
    "emails": [
      "support@company066.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "support@company066.example.com",
        "source_url": "https://www.company066.example.com/about/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company066.example.com/about/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业067有限公司",
    "website_url": "https://www.company067.example.com/",
    "industry_primary": "网络安全",
    "industry_secondary": "安全软件 / 企业安全服务",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "kefu@company067.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "kefu@company067.example.com",
        "source_url": "https://www.company067.example.com/about/contactus.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company067.example.com/about/contactus.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业068有限公司",
    "website_url": "https://www.company068.example.com/",
    "industry_primary": "人工智能",
    "industry_secondary": "计算机视觉 / AI平台",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "business@company068.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "business@company068.example.com",
        "source_url": "https://www.company068.example.com/contact/business",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company068.example.com/contact/business",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业069有限公司",
    "website_url": "https://www.company069.example.com/",
    "industry_primary": "企业软件",
    "industry_secondary": "通信软件 / 数字化转型",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "marketing@company069.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "marketing@company069.example.com",
        "source_url": "https://www.company069.example.com/en_us/ir_contactinfo.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company069.example.com/en_us/ir_contactinfo.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业070有限公司",
    "website_url": "https://www.company070.example.com/",
    "industry_primary": "电商平台",
    "industry_secondary": "B2B服务 / 企业采购",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company070.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company070.example.com",
        "source_url": "https://www.company070.example.com/frontView/jddd",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company070.example.com/frontView/jddd",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业071有限公司",
    "website_url": "https://www.company071.example.com/",
    "industry_primary": "ICT",
    "industry_secondary": "企业通信 / 云 / 数字基础设施",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company071.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company071.example.com",
        "source_url": "https://www.company071.example.com/cn/contact-us",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company071.example.com/cn/contact-us",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业072有限公司",
    "website_url": "https://www.company072.example.com/",
    "industry_primary": "硬件制造",
    "industry_secondary": "PC / 企业设备 / IT基础设施",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company072.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company072.example.com",
        "source_url": "https://www.company072.example.com/en/about/corpinfo.php",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company072.example.com/en/about/corpinfo.php",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业073有限公司",
    "website_url": "https://www.company073.example.com/",
    "industry_primary": "人工智能",
    "industry_secondary": "语音识别 / 大模型 / 教育AI",
    "province": "安徽省",
    "city": "合肥市",
    "emails": [
      "contact@company073.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company073.example.com",
        "source_url": "https://www.company073.example.com/static/privacAgreements.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company073.example.com/static/privacAgreements.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业074有限公司",
    "website_url": "https://www.company074.example.com/",
    "industry_primary": "人工智能",
    "industry_secondary": "计算机视觉 / 企业AI",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company074.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company074.example.com",
        "source_url": "https://www.company074.example.com/uploads/20240520/22e8980ec7da5eda36f881ac459f7ff9.pdf",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company074.example.com/uploads/20240520/22e8980ec7da5eda36f881ac459f7ff9.pdf",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业075有限公司",
    "website_url": "https://www.company075.example.com/",
    "industry_primary": "互联网",
    "industry_secondary": "云 / 企业服务 / 游戏",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company075.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company075.example.com",
        "source_url": "https://www.company075.example.com/zh-cn/contact/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company075.example.com/zh-cn/contact/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业076有限公司",
    "website_url": "https://www.company076.example.com/",
    "industry_primary": "ICT",
    "industry_secondary": "网络设备 / 企业IT",
    "province": "浙江省",
    "city": "杭州市",
    "emails": [
      "service@company076.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "service@company076.example.com",
        "source_url": "https://www.company076.example.com/cn/About_H3C/Contact_Us/Headquarters/Default.htm",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company076.example.com/cn/About_H3C/Contact_Us/Headquarters/Default.htm",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业077有限公司",
    "website_url": "https://www.company077.example.com/",
    "industry_primary": "电子制造",
    "industry_secondary": "显示面板 / 工业科技",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company077.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company077.example.com",
        "source_url": "https://www.company077.example.com/sustainability/BOE2024-sustainability-report.pdf",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company077.example.com/sustainability/BOE2024-sustainability-report.pdf",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业078有限公司",
    "website_url": "https://www.company078.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "智能硬件",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "service015@company078.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "service015@company078.example.com",
        "source_url": "https://www.company078.example.com/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company078.example.com/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业079有限公司",
    "website_url": "https://www.company079.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "自动化设备",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company079.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company079.example.com",
        "source_url": "https://www.company079.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company079.example.com/contact.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业080有限公司",
    "website_url": "https://www.company080.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "智能制造",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company080.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company080.example.com",
        "source_url": "https://www.company080.example.com/cn-about-capabilities.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company080.example.com/cn-about-capabilities.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业081有限公司",
    "website_url": "https://www.company081.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "智能制造",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "marketing@company081.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "marketing@company081.example.com",
        "source_url": "https://www.company081.example.com/honor",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company081.example.com/honor",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业082有限公司",
    "website_url": "https://www.company082.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "智能硬件",
    "province": "广东省",
    "city": "东莞市",
    "emails": [
      "contact@company082.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company082.example.com",
        "source_url": "https://www.company082.example.com/contactus/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company082.example.com/contactus/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业083有限公司",
    "website_url": "https://www.company083.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "新材料 / 铝型材",
    "province": "广东省",
    "city": "佛山市",
    "emails": [
      "sales@company083.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company083.example.com",
        "source_url": "https://www.company083.example.com/contact_us.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company083.example.com/contact_us.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业084有限公司",
    "website_url": "https://www.company084.example.com/",
    "industry_primary": "外贸B2B",
    "industry_secondary": "进出口贸易",
    "province": "浙江省",
    "city": "宁波市",
    "emails": [
      "contact@company084.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company084.example.com",
        "source_url": "https://www.company084.example.com/contact/Default.aspx",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company084.example.com/contact/Default.aspx",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业085有限公司",
    "website_url": "https://www.company085.example.com/",
    "industry_primary": "外贸B2B",
    "industry_secondary": "进出口贸易",
    "province": "浙江省",
    "city": "宁波市",
    "emails": [
      "sales@company085.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company085.example.com",
        "source_url": "https://www.company085.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company085.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业086有限公司",
    "website_url": "https://www.company086.example.com/",
    "industry_primary": "外贸B2B",
    "industry_secondary": "进出口贸易",
    "province": "浙江省",
    "city": "宁波市",
    "emails": [
      "contact@company086.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company086.example.com",
        "source_url": "https://www.company086.example.com/zh/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company086.example.com/zh/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业087有限公司",
    "website_url": "https://www.company087.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "工业设备",
    "province": "浙江省",
    "city": "杭州市",
    "emails": [
      "contact@company087.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company087.example.com",
        "source_url": "https://www.company087.example.com/contact_25/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company087.example.com/contact_25/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业088有限公司",
    "website_url": "https://www.company088.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "新材料",
    "province": "浙江省",
    "city": "杭州市",
    "emails": [
      "info@company088.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company088.example.com",
        "source_url": "https://www.company088.example.com/contacts/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company088.example.com/contacts/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业089有限公司",
    "website_url": "https://www.company089.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "电子元器件",
    "province": "福建省",
    "city": "厦门市",
    "emails": [
      "contact@company089.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company089.example.com",
        "source_url": "https://www.company089.example.com/default.aspx",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company089.example.com/default.aspx",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业090有限公司",
    "website_url": "https://www.company090.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "医疗器械",
    "province": "山东省",
    "city": "青岛市",
    "emails": [
      "sales@company090.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company090.example.com",
        "source_url": "https://www.company090.example.com/en/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company090.example.com/en/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业091有限公司",
    "website_url": "https://www.company091.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "医疗器械",
    "province": "山东省",
    "city": "青岛市",
    "emails": [
      "contact@company091.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company091.example.com",
        "source_url": "https://www.company091.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company091.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业092有限公司",
    "website_url": "https://www.company092.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "医疗器械",
    "province": "山东省",
    "city": "青岛市",
    "emails": [
      "info@company092.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company092.example.com",
        "source_url": "https://www.company092.example.com/about/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company092.example.com/about/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业093有限公司",
    "website_url": "https://www.company093.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "半导体",
    "province": "江苏省",
    "city": "无锡市",
    "emails": [
      "info@company093.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company093.example.com",
        "source_url": "https://www.company093.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company093.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业094有限公司",
    "website_url": "https://www.company094.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "集成电路",
    "province": "江苏省",
    "city": "无锡市",
    "emails": [
      "sales@company094.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company094.example.com",
        "source_url": "https://www.company094.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company094.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业095有限公司",
    "website_url": "https://www.company095.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "工业设备",
    "province": "江苏省",
    "city": "苏州市",
    "emails": [
      "sales@company095.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company095.example.com",
        "source_url": "https://www.company095.example.com/contactus/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company095.example.com/contactus/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业096有限公司",
    "website_url": "https://www.company096.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "自动化设备",
    "province": "江苏省",
    "city": "苏州市",
    "emails": [
      "sales@company096.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company096.example.com",
        "source_url": "https://www.company096.example.com/contact-us/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company096.example.com/contact-us/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业097有限公司",
    "website_url": "https://www.company097.example.com/",
    "industry_primary": "家电制造",
    "industry_secondary": "智能家居与消费电子",
    "province": "山东省",
    "city": "青岛市",
    "emails": [
      "contact@company097.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company097.example.com",
        "source_url": "https://www.company097.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company097.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业098有限公司",
    "website_url": "https://www.company098.example.com/",
    "industry_primary": "高端装备",
    "industry_secondary": "机械工程与工业制造",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "office@company098.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "office@company098.example.com",
        "source_url": "https://www.company098.example.com/lxwm/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company098.example.com/lxwm/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业099有限公司",
    "website_url": "https://www.company099.example.com/",
    "industry_primary": "工业制造",
    "industry_secondary": "气体储运与压力容器",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company099.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company099.example.com",
        "source_url": "https://www.company099.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company099.example.com/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业100有限公司",
    "website_url": "https://www.company100.example.com/",
    "industry_primary": "工业装备",
    "industry_secondary": "激光加工与智能制造",
    "province": "广东省",
    "city": "深圳市",
    "emails": [
      "contact@company100.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company100.example.com",
        "source_url": "https://www.company100.example.com/contact",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company100.example.com/contact",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业101有限公司",
    "website_url": "https://www.company101.example.com/",
    "industry_primary": "工业设备制造",
    "industry_secondary": "机械设备",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "info@company101.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company101.example.com",
        "source_url": "https://www.company101.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company101.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业102有限公司",
    "website_url": "https://www.company102.example.com/",
    "industry_primary": "机械设备贸易",
    "industry_secondary": "工业设备进出口",
    "province": "天津市",
    "city": "天津市",
    "emails": [
      "contact@company102.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company102.example.com",
        "source_url": "https://www.company102.example.com/Contact.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company102.example.com/Contact.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业103有限公司",
    "website_url": "https://www.company103.example.com/",
    "industry_primary": "机械设备制造",
    "industry_secondary": "工业机械",
    "province": "天津市",
    "city": "天津市",
    "emails": [
      "info@company103.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company103.example.com",
        "source_url": "https://www.company103.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company103.example.com/contact.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业104有限公司",
    "website_url": "https://www.company104.example.com/",
    "industry_primary": "石油装备制造",
    "industry_secondary": "油气设备",
    "province": "天津市",
    "city": "天津市",
    "emails": [
      "contact@company104.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company104.example.com",
        "source_url": "https://www.company104.example.com/contactus.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company104.example.com/contactus.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业105有限公司",
    "website_url": "https://www.company105.example.com/",
    "industry_primary": "锻压设备制造",
    "industry_secondary": "金属成形设备",
    "province": "天津市",
    "city": "天津市",
    "emails": [
      "contact@company105.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company105.example.com",
        "source_url": "https://www.company105.example.com/about/detail",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company105.example.com/about/detail",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业106有限公司",
    "website_url": "https://www.company106.example.com/",
    "industry_primary": "机械制造",
    "industry_secondary": "工业设备",
    "province": "山东省",
    "city": "青岛市",
    "emails": [
      "info@company106.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company106.example.com",
        "source_url": "https://www.company106.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company106.example.com/contact.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业107有限公司",
    "website_url": "https://www.company107.example.com/",
    "industry_primary": "工业自动化",
    "industry_secondary": "自动化设备",
    "province": "山东省",
    "city": "济南市",
    "emails": [
      "contact@company107.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company107.example.com",
        "source_url": "https://www.company107.example.com/lianxi/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company107.example.com/lianxi/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业108有限公司",
    "website_url": "https://www.company108.example.com/",
    "industry_primary": "工业检测设备",
    "industry_secondary": "医疗器械检测",
    "province": "山东省",
    "city": "济南市",
    "emails": [
      "info@company108.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company108.example.com",
        "source_url": "https://www.company108.example.com/products-347.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company108.example.com/products-347.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业109有限公司",
    "website_url": "https://www.company109.example.com/",
    "industry_primary": "医疗设备制造",
    "industry_secondary": "医疗器械",
    "province": "山东省",
    "city": "曲阜市",
    "emails": [
      "info@company109.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company109.example.com",
        "source_url": "https://www.company109.example.com/contact",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company109.example.com/contact",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业110有限公司",
    "website_url": "https://www.company110.example.com/",
    "industry_primary": "机械工业",
    "industry_secondary": "工业设备",
    "province": "辽宁省",
    "city": "大连市",
    "emails": [
      "info@company110.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company110.example.com",
        "source_url": "https://www.company110.example.com/CN/ContactUs/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company110.example.com/CN/ContactUs/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业111有限公司",
    "website_url": "https://www.company111.example.com/",
    "industry_primary": "专用设备制造",
    "industry_secondary": "冷冻设备",
    "province": "辽宁省",
    "city": "沈阳市",
    "emails": [
      "info@company111.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company111.example.com",
        "source_url": "https://www.company111.example.com/Contact.html",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company111.example.com/Contact.html",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业112有限公司",
    "website_url": "https://www.company112.example.com/",
    "industry_primary": "电机制造",
    "industry_secondary": "工业电机",
    "province": "辽宁省",
    "city": "沈阳市",
    "emails": [
      "contact@company112.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company112.example.com",
        "source_url": "https://www.company112.example.com/",
        "is_public_company_contact": true,
        "confidence": "low"
      }
    ],
    "source_url": "https://www.company112.example.com/",
    "confidence": "low",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业113有限公司",
    "website_url": "https://www.company113.example.com/",
    "industry_primary": "工业设备制造",
    "industry_secondary": "智能装备",
    "province": "陕西省",
    "city": "西安市",
    "emails": [
      "bd@company113.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "bd@company113.example.com",
        "source_url": "https://www.company113.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company113.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业114有限公司",
    "website_url": "https://www.company114.example.com/",
    "industry_primary": "机械设备制造",
    "industry_secondary": "矿山设备",
    "province": "河南省",
    "city": "郑州市",
    "emails": [
      "info@company114.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company114.example.com",
        "source_url": "https://www.company114.example.com/lianxi.htm",
        "is_public_company_contact": true,
        "confidence": "low"
      }
    ],
    "source_url": "https://www.company114.example.com/lianxi.htm",
    "confidence": "low",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业115有限公司",
    "website_url": "https://www.company115.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "机械设备/汽车零部件",
    "province": "浙江省",
    "city": "台州市",
    "emails": [
      "info@company115.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "info@company115.example.com",
        "source_url": "https://www.company115.example.com/aboutus/company/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company115.example.com/aboutus/company/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业116有限公司",
    "website_url": "https://www.company116.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "功能材料/工业材料",
    "province": "浙江省",
    "city": "温州市",
    "emails": [
      "contact@company116.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company116.example.com",
        "source_url": "https://www.company116.example.com/ContactUs.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company116.example.com/ContactUs.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业117有限公司",
    "website_url": "https://www.company117.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "高分子材料/化工材料",
    "province": "浙江省",
    "city": "绍兴市",
    "emails": [
      "sales@company117.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "sales@company117.example.com",
        "source_url": "https://www.company117.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company117.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业118有限公司",
    "website_url": "https://www.company118.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "工业材料/高分子材料",
    "province": "浙江省",
    "city": "嘉兴市",
    "emails": [
      "contact@company118.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company118.example.com",
        "source_url": "https://www.company118.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company118.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业119有限公司",
    "website_url": "https://www.company119.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "工业装备/自动化设备",
    "province": "江苏省",
    "city": "宿迁市",
    "emails": [
      "contact@company119.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company119.example.com",
        "source_url": "https://www.company119.example.com/lxwm",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company119.example.com/lxwm",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业120有限公司",
    "website_url": "https://www.company120.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "机电设备/工业配套",
    "province": "江苏省",
    "city": "泰州市",
    "emails": [
      "contact@company120.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company120.example.com",
        "source_url": "https://www.company120.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company120.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业121有限公司",
    "website_url": "https://www.company121.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "磁性材料/功能材料",
    "province": "安徽省",
    "city": "阜阳市",
    "emails": [
      "contact@company121.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company121.example.com",
        "source_url": "https://www.company121.example.com/lianxiwomen",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company121.example.com/lianxiwomen",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业122有限公司",
    "website_url": "https://www.company122.example.com/",
    "industry_primary": "新材料",
    "industry_secondary": "工业材料/化工材料",
    "province": "安徽省",
    "city": "安庆市",
    "emails": [
      "contact@company122.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company122.example.com",
        "source_url": "https://www.company122.example.com/contact/",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company122.example.com/contact/",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业123有限公司",
    "website_url": "https://www.company123.example.com/",
    "industry_primary": "制造业",
    "industry_secondary": "标准件/紧固件",
    "province": "重庆市",
    "city": "重庆市",
    "emails": [
      "contact@company122.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company122.example.com",
        "source_url": "https://www.company123.example.com/a/gongsigaikuang/jituanjianjie/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company123.example.com/a/gongsigaikuang/jituanjianjie/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业124有限公司",
    "website_url": "https://www.company124.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "工业自动化/工业控制",
    "province": "福建省",
    "city": "福州市",
    "emails": [
      "contact@company124.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company124.example.com",
        "source_url": "https://www.company124.example.com/CN/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company124.example.com/CN/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业125有限公司",
    "website_url": "https://www.company125.example.com/",
    "industry_primary": "电子信息",
    "industry_secondary": "智能硬件/边缘计算",
    "province": "福建省",
    "city": "福州市",
    "emails": [
      "service@company125.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "service@company125.example.com",
        "source_url": "https://www.company125.example.com/",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company125.example.com/",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业126有限公司",
    "website_url": "https://www.company126.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "机器人/工业自动化",
    "province": "福建省",
    "city": "厦门市",
    "emails": [
      "contact@company126.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company126.example.com",
        "source_url": "https://www.company126.example.com/contact.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company126.example.com/contact.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业127有限公司",
    "website_url": "https://www.company127.example.com/",
    "industry_primary": "智能制造",
    "industry_secondary": "工业自动化/智能装备",
    "province": "广西壮族自治区",
    "city": "柳州市",
    "emails": [
      "contact@company127.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company127.example.com",
        "source_url": "https://www.company127.example.com/ppwh",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company127.example.com/ppwh",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业128有限公司",
    "website_url": "https://www.company128.example.com/",
    "industry_primary": "能源化工",
    "industry_secondary": "新材料/工业园区配套",
    "province": "广西壮族自治区",
    "city": "钦州市",
    "emails": [
      "contact@company128.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company128.example.com",
        "source_url": "https://www.company128.example.com/about",
        "is_public_company_contact": true,
        "confidence": "medium"
      }
    ],
    "source_url": "https://www.company128.example.com/about",
    "confidence": "medium",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业129有限公司",
    "website_url": "https://www.company129.example.com/",
    "industry_primary": "工业制造",
    "industry_secondary": "煤矿机械与矿山装备",
    "province": "北京市",
    "city": "北京市",
    "emails": [
      "contact@company129.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company129.example.com",
        "source_url": "https://www.company129.example.com/col/col1052/index.html",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company129.example.com/col/col1052/index.html",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  },
  {
    "canonical_name": "示例企业130有限公司",
    "website_url": "https://www.company130.example.com/",
    "industry_primary": "重工装备",
    "industry_secondary": "港口机械与大型起重装备",
    "province": "上海市",
    "city": "上海市",
    "emails": [
      "contact@company130.example.com"
    ],
    "contacts": [
      {
        "contact_type": "email",
        "contact_value": "contact@company130.example.com",
        "source_url": "https://www.company130.example.com/contactus.php",
        "is_public_company_contact": true,
        "confidence": "high"
      }
    ],
    "source_url": "https://www.company130.example.com/contactus.php",
    "confidence": "high",
    "missing_fields": [],
    "collected_at": "2026-08-20"
  }
];

export const SALES_AGENT_SEED_COMPANY_COUNT = SALES_AGENT_SEED_COMPANIES.length;

export function getSalesAgentSeedCompanyPrimaryEmail(company: SalesAgentSeedCompany): string {
  return company.contacts.find(contact => contact.contact_type === 'email')?.contact_value || company.emails[0] || '';
}
