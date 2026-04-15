// Translation resources are inlined in this file intentionally.
// Do NOT add src/i18n/locales/*.json — it will NOT be loaded.
// If you need to add/change translation keys, edit the en/zh resources
// objects below directly. Full rationale: docs/i18n-status.md §1.

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// 检查 i18next 是否已初始化
if (!i18n.isInitialized) {
  i18n.use(initReactI18next).init({
    resources: {
      en: {
        translation: {
          "nav": {
            "home": "Home",
            "geoKnowledge": "GEO Knowledge",
            "productsServices": "Products & Services",
            "aboutUs": "About Us",
            "about": "About GEO",
            "process": "Process",
            "pricing": "Pricing",
            "data": "Insights",
            "contact": "Contact",
            "langSwitch": "中文",
            "login": "Log in",
            "register": "Sign up",
            "signedInAs": "Signed in as",
            "account": "Account",
            "logout": "Sign out"
          },
          "common": {
            "error": "Error",
            "loading": "Loading...",
            "contact": "Login",
            "cancel": "Logout",
            "success": "Success",
            "theme": {
              "switchToLight": "Switch to light mode",
              "switchToDark": "Switch to dark mode"
            },
            "errors": {
              "loadFailed": "Failed to load content",
              "loadFailedWith": "Failed to load {{entity}}",
              "deleteFailed": "Failed to delete",
              "paymentFailed": "Payment failed",
              "genericFailed": "Operation failed"
            }
          },
          "hero": {
            "title": "Get AI to Recommend Your Brand",
            "subtitle": "Generative Engine Optimization (GEO)",
            "description": "Ensure your business is recommended first when potential customers ask ChatGPT, Gemini, Perplexity, and other AI assistants for product or service recommendations.",
            "cta": "Learn More",
            "ctaSecondary": "Contact Us"
          },
          "about": {
            "sectionTag": "About GEO",
            "title": "What is Generative Engine Optimization?",
            "description": "GEO (Generative Engine Optimization) is the process of getting businesses recommended by generative AI chatbots. It is an emerging technology that optimizes content for generative AI search engines (such as ChatGPT, Perplexity AI, Gemini, DeepSeek Search, as well as future AI-powered search from Baidu and Google).",
            "definition": "GEO Definition",
            "definitionText": "GEO is the process of getting your company suggested by generative AI chatbots when prospective customers ask for product or service recommendations, and this process can be tailored for specific generative AI search engines such as ChatGPT Optimization and Perplexity Optimization.",
            "combination": "Three Pillars",
            "combinationText": "GEO services are a combination of SEO, PR, and reputation management. The ideal GEO service package involves the following activities as a core strategy: superlative list ranking, website SEO optimization, review reputation management, and PR outreach.",
            "difference": "GEO vs Traditional SEO",
            "differenceText": "Traditional SEO focuses on getting your website to rank high in search engine results, while GEO focuses on getting your brand recommended in AI-generated content. With the rise of AI search, GEO is becoming an essential part of digital marketing."
          },
          "process": {
            "sectionTag": "Our Process",
            "title": "Four-Step GEO Strategy",
            "subtitle": "Our GEO services cover the following four core steps to ensure your brand gets maximum exposure in the AI era.",
            "step1": {
              "title": "Superlative List Ranking",
              "description": "Secure a high placement on superlative lists that rank well on Google. For example, for a fleet management software company, secure a top spot in a list article entitled \"The Top-Ranked Fleet Management Software of 2025.\" This can be accomplished via PR, paying for placement, or publishing your own superlative list article and ranking your company first."
            },
            "step2": {
              "title": "Website SEO Optimization",
              "description": "Perform SEO on your website so that your list articles rank highly. If you publish list articles for every category in which you sell products or services and they rank in the top 3 search results, you are much more likely to be recommended by ChatGPT, Gemini, and Perplexity."
            },
            "step3": {
              "title": "Review Reputation Management",
              "description": "Ensure good reviews on the most popular review websites. For technology services, these sites include G2 and Clutch; for software, Capterra and PC Magazine; for travel and leisure, TripAdvisor and Yelp; for consumer technology, CNet and Consumer Reports. B2B companies should also pay attention to employee review websites such as Glassdoor and Indeed."
            },
            "step4": {
              "title": "PR Outreach",
              "description": "Generate positive publicity via traditional PR. Generative AI chatbots are more likely to recommend a company if it's lauded in authoritative publications such as the Wall Street Journal and New York Times."
            }
          },
          "pricing": {
            "sectionTag": "Pricing",
            "title": "Choose Your GEO Plan",
            "subtitle": "We offer three tiers of GEO services to meet the needs of businesses of different sizes.",
            "tier1": {
              "name": "Starter",
              "price": "$2,000",
              "priceEnd": "–$3,000",
              "period": "/month",
              "description": "Ideal for businesses just starting with GEO",
              "features": [
                "Purchases low-cost placements on ranking websites",
                "Basic list placement service",
                "Monthly performance report"
              ],
              "notIncluded": [
                "Superlative List SEO",
                "Reputation Management",
                "PR"
              ]
            },
            "tier2": {
              "name": "Professional",
              "price": "$4,000",
              "priceEnd": "–$7,000",
              "period": "/month",
              "description": "For businesses looking to systematically advance their GEO",
              "features": [
                "Monitors ranking website placement costs to determine most impactful placement spend",
                "Creates & publishes 1-2 superlative list articles at the beginning of a campaign",
                "Monthly performance report & strategy adjustment",
                "Keyword ranking tracking"
              ],
              "notIncluded": [
                "Reputation Management",
                "PR"
              ]
            },
            "tier3": {
              "name": "Enterprise",
              "price": "$8,000",
              "priceEnd": "–$12,000",
              "period": "/month",
              "description": "Comprehensive GEO service for maximum AI recommendation impact",
              "features": [
                "Monitors ranking website placement costs to determine most impactful placement spend",
                "Creates & publishes 3-5 superlative list articles per month, supported by metrics articles, authority statement placement, and validation pages",
                "Facilitates positive reviews from customers/clients and minimize the impact of negative reviews",
                "Ensures descriptions in major industry directories reflect industry leadership",
                "Works with either internal staff or a dedicated PR agency to secure media coverage",
                "Dedicated strategy consultant"
              ],
              "notIncluded": []
            },
            "cta": "Get Started"
          },
          "data": {
            "sectionTag": "Insights",
            "title": "GEO Key Metrics",
            "subtitle": "Understand the latest trends in generative AI search and the impact of GEO.",
            "stat1": {
              "value": "65%",
              "label": "Users search for product recommendations via AI"
            },
            "stat2": {
              "value": "3x",
              "label": "Brand recommendation rate increase after GEO"
            },
            "stat3": {
              "value": "5+",
              "label": "Major AI platforms covered"
            },
            "stat4": {
              "value": "89%",
              "label": "Client satisfaction rate"
            },
            "platforms": {
              "title": "AI Platforms We Cover"
            }
          },
          "contact": {
            "sectionTag": "Contact Us",
            "title": "Start Your GEO Journey",
            "subtitle": "Fill in the information below and our GEO experts will contact you within 24 hours.",
            "name": "Name",
            "email": "Email",
            "company": "Company",
            "message": "Message",
            "submit": "Submit Inquiry",
            "info": {
              "email": "geo@zen7.com",
              "phone": "+86 400-XXX-XXXX"
            },
            "form": {
              "name": "Name",
              "email": "Email",
              "website": "Website",
              "message": "Message",
              "submit": "Submit",
              "sending": "Sending...",
              "success": "Your message has been sent successfully! We will contact you soon.",
              "error": "There was an error sending your message. Please try again later.",
              "errors": {
                "name": "Please enter your name",
                "email": "Please enter a valid email address",
                "website": "Please enter a valid website URL",
                "message": "Please enter your message"
              },
              "placeholders": {
                "name": "Your name",
                "email": "Your email",
                "website": "Your website",
                "message": "Your message"
              }
            },
            "otherWays": "Other Ways to Contact Us",
            "backToHome": "Back to Home",
            "faq": {
              "title": "Frequently Asked Questions",
              "questions": {
                "question1": "What is GEO and how does it differ from SEO?",
                "question2": "How long does it take to see results from GEO optimization?",
                "question3": "What AI platforms do you optimize for?",
                "question4": "How much does GEO optimization cost?",
                "question5": "Do you offer ongoing GEO maintenance?"
              },
              "answers": {
                "answer1": "GEO (Generative Engine Optimization) focuses on getting your brand recommended by AI chatbots like ChatGPT, Gemini, and Perplexity, while traditional SEO focuses on ranking high in search engine results. GEO is specifically designed for the new era of AI-powered search.",
                "answer2": "Results can vary depending on your industry and competition, but most clients start seeing improvements within 1-3 months. Full optimization typically takes 3-6 months to see maximum results.",
                "answer3": "We optimize for all major AI platforms including ChatGPT, Gemini, Perplexity, DeepSeek, and future AI-powered search from Google and Baidu.",
                "answer4": "Our GEO services range from $2,000-$12,000 per month depending on the scope and complexity of your project. We offer customized plans to fit different business needs and budgets.",
                "answer5": "Yes, we offer ongoing GEO maintenance to ensure your brand continues to perform well as AI algorithms evolve. Our maintenance plans include regular monitoring, updates, and strategy adjustments."
              }
            }
          },
          "footer": {
            "description": "Zen7 is a professional Generative Engine Optimization (GEO) service provider, helping businesses achieve maximum brand exposure in the AI era.",
            "quickLinks": "Quick Links",
            "contactInfo": "Contact Info",
            "copyright": "© 2026 Zen7. All rights reserved."
          },
          "geoTool": {
            "title": "Want to check your website GEO readiness?",
            "description": "Use our free GEO Readiness Checker to analyze your website.",
            "cta": "Check Your Website"
          },
          "home": {
            "title": "GEO Readiness Checker",
            "description": "Optimize your website for AI-powered search engines and assistants. Get detailed insights and actionable recommendations.",
            "placeholder": "moltspay.com",
            "button": "Check GEO Readiness",
            "analyzing": "Analyzing...",
            "error": {
              "empty": "Please enter a URL",
              "invalid": "Please enter a valid URL",
              "failed": "Check failed. Please try again.",
              "quotaExceeded": "You've used all your free checks this month. Upgrade a plan to keep checking.",
              "quotaCta": "View plans"
            },
            "poweredBy": "Powered by GEO AI",
            "contactLink": "Need expert optimization help?",
            "advanced": {
              "badge": "Members Only",
              "title": "Advanced Detection",
              "subtitle": "Unlock professional GEO tools to measure how AI engines see your brand across the web.",
              "upgrade": "Upgrade to unlock",
              "comingSoon": "Coming soon",
              "tierModal": {
                "title": "Choose Your Plan",
                "subtitle": "Unlock advanced detection features by selecting a membership plan."
              },
              "validation": {
                "minUrls": "Enter at least 2 URLs to compare.",
                "invalidUrl": "Invalid URL: {{url}}",
                "entityRequired": "Entity name is required.",
                "unexpected": "Unexpected error, please try again later."
              },
              "cards": {
                "compare": {
                  "title": "Competitive Comparison",
                  "desc": "Benchmark multiple sites side-by-side across all GEO categories."
                },
                "crawlTest": {
                  "title": "AI Crawler Test",
                  "desc": "Verify that GPTBot, ClaudeBot and others can actually reach your pages."
                },
                "authority": {
                  "title": "Authority Audit",
                  "desc": "Evaluate reviews, mentions, and third-party signals AI engines trust."
                },
                "citation": {
                  "title": "AI Citation Check",
                  "desc": "See whether AI engines cite your site when asked brand-relevant questions."
                },
                "visibility": {
                  "title": "AI Visibility Audit",
                  "desc": "Full multi-engine visibility report across Perplexity, ChatGPT and Claude."
                },
                "entity": {
                  "title": "Entity GEO Audit",
                  "desc": "Audit how AI recognizes a brand, product, or person — no URL required."
                }
              },
              "result": {
                "lead": "lead",
                "compare": {
                  "categoryCompare": "Category score comparison",
                  "sitesCategories": "{{sites}} sites · {{categories}} categories",
                  "category": "Category",
                  "total": "Total"
                },
                "crawl": {
                  "targetDomain": "Target domain",
                  "ccFound": "Indexed by Common Crawl",
                  "ccNotFound": "Not in Common Crawl",
                  "totalIssues": "Total issues",
                  "allClear": "All clear",
                  "needsFix": "Needs fixes",
                  "robotsAllowed": "robots allowed",
                  "crawlerPermission": "Crawler permission",
                  "wafAllowed": "WAF allowed",
                  "liveAccess": "Live access",
                  "robotsTitle": "robots.txt rules",
                  "detected": "Found",
                  "notFound": "Not found",
                  "robotsMissingWarning": "robots.txt missing — all crawlers are allowed by default",
                  "wafTitle": "WAF / CDN live test",
                  "wafBaseline": "baseline {{status}} · {{size}}KB",
                  "commonCrawlTitle": "Common Crawl index",
                  "pagesSuffix": "{{count}} pages",
                  "notIndexed": "Not indexed",
                  "foundInCcPrefix": "Found ",
                  "foundInCcSuffix": " pages in Common Crawl"
                },
                "citation": {
                  "cited": "Cited",
                  "queries": "Queries",
                  "citationCount": "Citation count",
                  "directCitations": "Direct citations",
                  "grade": "Grade",
                  "overallScore": "Overall score",
                  "perQuery": "Per-query results",
                  "queriesSuffix": "{{count}} queries"
                },
                "visibility": {
                  "queryBreakdown": "{{count}} queries × {{runs}} stability runs",
                  "perEngineRate": "Per-engine visibility",
                  "competitors": "Co-mentioned competitors",
                  "noCompetitors": "No competitors detected.",
                  "framings": "Brand sentiment framings",
                  "contentGaps": "Content gaps",
                  "noGaps": "No major gaps"
                },
                "entity": {
                  "kgTitle": "Knowledge graph coverage",
                  "platforms": "Platform coverage",
                  "sentimentTitle": "Sentiment & framing",
                  "overallSentiment": "Overall sentiment",
                  "bestFraming": "Best framing",
                  "recognitionRate": "Recognition rate",
                  "contentGaps": "Content gaps",
                  "noGaps": "No gaps found"
                }
              }
            },
            "buttons": {
              "geoKnowledge": "Learn about GEO Knowledge",
              "services": "View Service Packages"
            }
          },
          "result": {
            "title": "GEO Readiness Results",
            "resultsFor": "Results for:",
            "scoreCard": {
              "title": "AI Visibility Score",
              "description": "How well your website is optimized for AI search",
              "grade": "Grade"
            },
            "summary": {
              "passed": "Passed",
              "warnings": "Warnings",
              "failed": "Failed",
              "info": "Info",
              "totalChecks": "Total Checks"
            },
            "detailedResults": "Detailed Results",
            "fix": "Fix:",
            "buttons": {
              "checkAnother": "Check Another Website",
              "getHelp": "Get Optimization Help"
            },
            "error": {
              "noData": "No GEO check data found. Please run a check first."
            },
            "loginToView": "Login to View Full Results",
            "loginToViewDesc": "Log in to see all detailed results and recommendations",
            "loginButton": "Log In",
            "paywall": {
              "lockedCount": "{{count}} more checks locked",
              "subtitle": "Activate a membership to unlock every check and detailed fix recommendation.",
              "viewAll": "View all →",
              "perCategoryHint": "View 2 of {{total}} checks (membership unlocks all)"
            },
            "groupProgress": {
              "title": "Category Progress"
            },
            "categories": {
              "infraProtocols": "Protocols & Crawlability",
              "pageBasics": "Page Basics & Mobile",
              "aiProtocols": "AI-Specific Protocols",
              "structuredSemantic": "Structured & Semantic Data",
              "contentQuality": "Content Quality & Readability",
              "techRobustness": "Technical Robustness & Media",
              "authorityExternal": "Authority & External Signals",
              "other": "Other"
            },
            "categoryLabels": {
              "HTTPS": "HTTPS",
              "robots.txt": "robots.txt",
              "llms.txt": "llms.txt",
              ".well-known Discovery": ".well-known Discovery",
              "sitemap.xml": "Sitemap (sitemap.xml)",
              "Search Engine & AI Platform Registration": "Search Engine & AI Platform Registration",
              "Structured Data": "Structured Data (JSON-LD)",
              "Meta Tags": "Meta Tags",
              "Content Accessibility": "Content Accessibility",
              "AI Crawl Readiness": "AI Crawl Readiness",
              "Content Quality for AI": "Content Quality for AI",
              "Technical Crawlability": "Technical Crawlability",
              "Authority & Trust Signals": "Authority & Trust Signals",
              "AI-Specific Optimization": "AI-Specific Optimization",
              "Social Signals": "Social Signals",
              "AI Answer Format Optimization": "AI Answer Format Optimization",
              "Schema Breadcrumbs & Knowledge Panel": "Schema Breadcrumbs & Knowledge Panel",
              "Mobile-Friendliness & Page Weight": "Mobile-Friendliness & Page Weight",
              "URL Normalization": "URL Normalization",
              "Outbound Links & Media": "Outbound Links & Media",
              "Multilingual Content Depth": "Multilingual Content Depth",
              "Cross-Platform Content Distribution": "Cross-Platform Content Distribution",
              "Multi-Page Sampling": "Multi-Page Sampling"
            },
            "header": {
              "rerunPlaceholder": "Enter a URL to re-run the check",
              "rerun": "Re-run check",
              "modeLabel": "Detection mode",
              "modeDefault": "Standard check",
              "modeLockedHint": "Upgrade your plan to unlock this mode",
              "placeholderCompare": "https://a.com, https://b.com, https://c.com",
              "placeholderKeywords": "Keywords (optional, comma-separated)",
              "placeholderEntity": "Brand / product / person name",
              "entityTypeLabel": "Entity type",
              "entityType": {
                "brand": "Brand",
                "product": "Product",
                "person": "Person"
              },
              "compareHint": "Separate 2-5 URLs with commas or spaces",
              "visibilityHint": "Leave keywords blank to auto-generate; separate multiple with commas",
              "entityHint": "Enter the brand, product or person you want to audit"
            },
            "visuals": {
              "robots": {
                "title": "AI Crawler Permissions",
                "filePresent": "robots.txt · file present",
                "fileMissing": "robots.txt · file missing",
                "sitemapRef": "sitemap ref ✓",
                "noSitemapRef": "no sitemap ref",
                "wildcardWarning": "Wildcard rule User-agent: * blocks every crawler. Bots shown as \"inherit\" below are effectively blocked until you override them.",
                "legend": {
                  "allowed": "Allowed",
                  "blocked": "Blocked",
                  "inherited": "Inherits wildcard",
                  "unknown": "Unknown"
                }
              },
              "meta": {
                "title": "Meta Tag Coverage",
                "subtitle": "6 signals AI engines rely on for summaries",
                "passCount": "{{pass}}/{{total}} pass"
              },
              "platform": {
                "titleCross": "Cross-Platform Presence",
                "titleSocial": "Social Signal Coverage",
                "subtitle": "Platforms AI engines train on",
                "notDetected": "{{name}} not detected"
              }
            },
            "shareExport": {
              "title": "Share & Export",
              "copied": "Link copied to clipboard!",
              "copyLink": "Copy Link",
              "exportPDF": "Export PDF",
              "exportPDFLoading": "Exporting PDF...",
              "exportCSV": "Export CSV",
              "shareSocial": "Share to social media",
              "share": "Share",
              "downloadReport": "Download Report",
              "downloadReportLoading": "Generating report…"
            },
            "pdfReport": {
              "title": "GEO Readiness Report",
              "subtitle": "How ready your site is for AI-powered search engines",
              "targetSite": "Target Site",
              "generatedAt": "Generated At",
              "tier": "Report Tier",
              "overallScore": "Overall Score",
              "scoreInterpretation": "Score Interpretation",
              "scoreLevels": {
                "excellent": "Excellent — your site is well-optimized for AI search engines. Keep monitoring and fine-tuning.",
                "good": "Good — most fundamentals are in place. A few targeted fixes will lift you into the top tier.",
                "average": "Average — several important signals are missing. Prioritize the fixes below to improve visibility.",
                "poor": "Needs work — AI engines are likely underrepresenting your content. Address the failed checks soon.",
                "critical": "Critical — your site is largely invisible to AI engines. A full optimization pass is strongly recommended."
              },
              "groupSection": "Category Breakdown",
              "groupLabel": "Category",
              "recommendationsSection": "Priority Recommendations",
              "topFixesIntro": "The highest-impact issues, sorted by severity. Fixing these first yields the biggest score gains.",
              "detailSection": "Detailed Findings",
              "appendixSection": "About This Report",
              "appendixBody": "Generated by GEO Checker. Scores reflect how well your site follows best practices for AI-powered search engines like ChatGPT, Perplexity, Google AI Overviews, and Copilot. Re-run after making changes to see your progress.",
              "footer": "GEO Checker · © {{year}} · All rights reserved",
              "fileName": "geo-readiness-report",
              "fixLabel": "Fix",
              "noFix": "No specific fix required.",
              "noFailItems": "No failed or warning items found. Great work!",
              "lockedNotice": "{{count}} category group(s) are locked in this tier. Upgrade to unlock full results.",
              "statusLabels": {
                "pass": "Pass",
                "warn": "Warn",
                "fail": "Fail",
                "info": "Info"
              },
              "tierLabels": {
                "free": "Free",
                "pro": "Detector",
                "starter": "Starter",
                "growth": "Growth",
                "scale": "Scale"
              },
              "pageOf": "Page {{current}} / {{total}}",
              "coverBadge": "GEO READINESS REPORT",
              "headerSite": "Report for"
            }
          },
          "login": {
            "title": "Login",
            "subtitle": "Welcome back! Please log in to your account",
            "createAccount": "Create New Account",
            "email": "Email",
            "emailPlaceholder": "Enter your email",
            "password": "Password",
            "passwordPlaceholder": "Enter your password",
            "rememberMe": "Remember me",
            "forgotPassword": "Forgot password?",
            "button": "Log in",
            "loading": "Logging in...",
            "noAccount": "Don't have an account?",
            "register": "Sign up now",
            "failed": "Login failed. Please check your email and password",
            "error": "Invalid email or password"
          },
          "register": {
            "title": "Sign up",
            "subtitle": "Join us and start your GEO optimization journey",
            "loginAccount": "Login to Existing Account",
            "name": "Name",
            "namePlaceholder": "Your name",
            "email": "Email",
            "emailPlaceholder": "Enter your email",
            "password": "Password",
            "passwordPlaceholder": "Enter your password",
            "confirmPassword": "Confirm Password",
            "confirmPasswordPlaceholder": "Enter your password again",
            "terms": "I agree to the terms of service and privacy policy",
            "termsRequired": "Please agree to the terms of service and privacy policy",
            "button": "Sign up",
            "loading": "Signing up...",
            "passwordMismatch": "Passwords do not match",
            "failed": "Registration failed",
            "hasAccount": "Already have an account?",
            "login": "Log in now",
            "passwordHint": "Password must be at least 6 characters",
            "error": "Passwords do not match"
          },
          "forgotPassword": {
            "title": "Reset Password",
            "subtitle": "Enter your email and we'll send you a reset link",
            "button": "Send Reset Link",
            "loading": "Sending...",
            "success": "Reset link sent to your email. Please check your inbox",
            "failed": "Failed to send. Please check if the email is correct",
            "error": {
              "sendFailed": "Failed to send password reset email",
              "resetFailed": "Failed to reset password"
            },
            "backToLogin": "Back to login",
            "reset": {
              "title": "Reset Password",
              "description": "Enter your new password below",
              "newPassword": "New Password",
              "newPasswordPlaceholder": "Enter your new password",
              "confirmPassword": "Confirm Password",
              "confirmPasswordPlaceholder": "Confirm your new password",
              "button": "Reset Password",
              "loading": "Resetting..."
            }
          },
          "checkoutPending": {
            "title": "Pending Order",
            "subtitle": "Please confirm your order details before payment",
            "loading": "Loading order...",
            "missingSlug": "Missing plan identifier",
            "notFound": "Plan not found",
            "notSubscribable": "This plan is not directly subscribable. Please contact sales.",
            "domesticPending": "WeChat / Alipay payment is coming soon. Please contact sales to complete your subscription.",
            "payError": "Failed to start payment",
            "backToPlans": "Back to plans",
            "planLabel": "Subscription Plan",
            "popular": "Popular",
            "totalLabel": "Total Amount",
            "methodLabel": "Payment Method",
            "methodStripe": "You will be redirected to Stripe to complete the payment securely.",
            "methodDomestic": "Click \"Pay Now\" to create your order. WeChat / Alipay payment is coming soon.",
            "submitting": "Redirecting to payment...",
            "payNow": "Pay Now",
            "cancel": "Cancel"
          },
          "aboutUs": {
            "title": "About Us",
            "description": "A technology service provider specializing in GEO (Generative Engine Optimization), helping websites perform better in the generative AI era.",
            "story": {
              "title": "Our Story",
              "subtitle": "From Technological Innovation to Industry Leadership",
              "paragraph1": "Our team consists of experts passionate about AI and search engine technology. In the era of generative AI technology explosion, we saw new opportunities and challenges in website optimization.",
              "paragraph2": "In 2023, we began researching GEO (Generative Engine Optimization) technology, aiming to help websites perform better in the generative AI era. After a year of hard work, we developed a complete GEO detection and optimization system, providing professional technical support for enterprise and personal websites.",
              "paragraph3": "Today, we have become a leading service provider in the GEO field, providing professional GEO detection and optimization services to many clients, helping them achieve better online performance in the generative AI era."
            },
            "mission": {
              "title": "Our Mission",
              "innovation": {
                "title": "Technological Innovation",
                "description": "Continuously explore and innovate GEO technology, providing clients with the most advanced detection and optimization solutions."
              },
              "value": {
                "title": "Customer Value",
                "description": "Customer-centric, providing high-quality GEO services to help clients gain competitive advantages in the generative AI era."
              },
              "leadership": {
                "title": "Industry Leadership",
                "description": "Become an industry leader in the GEO field, promoting the establishment and development of industry standards."
              }
            },
            "team": {
              "title": "Our Team",
              "ceo": {
                "name": "Zhang Ming",
                "title": "Founder & CEO",
                "description": "Former Google engineer with 10 years of experience in search engine and AI technology, focusing on GEO technology research and application."
              },
              "cto": {
                "name": "Li Ting",
                "title": "Technical Director",
                "description": "Former Microsoft R&D engineer with 8 years of AI and machine learning experience, responsible for the technical architecture of the GEO detection system."
              },
              "marketing": {
                "name": "Wang Qiang",
                "title": "Marketing Director",
                "description": "Former Baidu marketing expert with 7 years of digital marketing experience, responsible for the company's marketing strategy and client development."
              },
              "design": {
                "name": "Zhao Fang",
                "title": "Design Director",
                "description": "Former Tencent UI/UX designer with 6 years of user experience design experience, responsible for product interface design and user experience."
              }
            },
            "contact": {
              "title": "Contact Us",
              "info": {
                "title": "Contact Information",
                "address": "Address",
                "addressValue": "Zhongguancun Science and Technology Park, Haidian District, Beijing",
                "email": "Email",
                "emailValue": "contact@geochecker.com",
                "phone": "Phone",
                "phoneValue": "+86 10 8888 8888"
              },
              "form": {
                "title": "Send Message",
                "name": "Name",
                "namePlaceholder": "Your name",
                "email": "Email",
                "emailPlaceholder": "Your email",
                "message": "Message",
                "messagePlaceholder": "Please enter your message",
                "button": "Send Message"
              }
            }
          },
          "geoKnowledge": {
            "title": "GEO Knowledge Center",
            "description": "Learn what Generative Engine Optimization is, why it replaces traditional SEO in the AI era, and how to get your brand recommended by ChatGPT, Perplexity, Google AI Overviews, Gemini, Claude and Copilot.",
            "sections": {
              "about": "About GEO",
              "whatIsGeo": "What is GEO?",
              "whatIsGeoBody": "GEO — Generative Engine Optimization — is the practice of optimizing your website, brand and content so that AI search engines and assistants (ChatGPT, Perplexity, Google AI Overviews, Gemini, Claude, Copilot) recognize, trust and recommend you when users ask for products, services or expert information. Where classic SEO targets the blue links on a search results page, GEO targets the generated answer itself.",
              "whyGeoImportant": "Why is GEO important?",
              "whyGeoPoints": [
                "Traditional search traffic is being replaced by AI-generated answers — if you are not in the answer, you are invisible",
                "AI engines cite only a handful of trusted sources per query; appearing in that set captures category-defining mindshare",
                "Once an AI model learns your brand as an entity, it recommends you across many related queries — the effect compounds",
                "Early GEO movers lock in category authority before competitors catch up"
              ],
              "strategies": "GEO Strategies",
              "contentLocalization": "Authoritative Content & Entity Clarity",
              "contentLocalizationDesc": "AI engines cite sources they can verify. Build comprehensive first-party content about your brand, products and category with clear entity signals that large language models can parse and trust.",
              "contentLocalizationPoints": [
                "Publish in-depth, expert-grade content on your core topics — avoid thin marketing copy",
                "Add Organization / Product structured data (JSON-LD) and an llms.txt file",
                "Establish brand presence on Wikipedia, Wikidata, Reddit, GitHub and authoritative directories"
              ],
              "technicalOptimization": "Technical Foundations & AI Crawlability",
              "technicalOptimizationDesc": "If AI crawlers cannot reach your content, they cannot cite you. Make sure GPTBot, ClaudeBot, PerplexityBot and Google-Extended can actually fetch and render your pages.",
              "technicalOptimizationPoints": [
                "Allow AI crawlers in robots.txt (GPTBot, ClaudeBot, PerplexityBot, Google-Extended)",
                "Serve fast, server-rendered HTML — avoid JS-only pages that AI bots cannot execute",
                "Use semantic HTML and clean markup so LLMs can parse your pages into structured facts"
              ],
              "keyData": "Key GEO Metrics",
              "importantMetrics": "What to measure",
              "regionalTraffic": "AI Citation Rate",
              "regionalTrafficDesc": "How often your site is linked as a source when AI engines answer brand- or category-relevant queries.",
              "languagePreference": "Answer Inclusion Rate",
              "languagePreferenceDesc": "The share of relevant queries where your brand is actually named in the AI-generated answer, with or without a direct citation link.",
              "searchTrends": "Competitor Share of Voice",
              "searchTrendsDesc": "Which competitors AI models name alongside you and how often — the real benchmark for category mindshare."
            },
            "tabs": {
              "overview": "Overview",
              "metrics": "Metrics Glossary"
            },
            "metrics": {
              "title": "GEO Metrics Glossary",
              "description": "Every check in the GEO Readiness report, explained plainly: what it measures, why it matters for AI visibility, and how to improve it.",
              "field": {
                "measures": "What it measures",
                "why": "Why it matters",
                "scoring": "Scoring logic"
              },
              "categories": {
                "crawlability": {
                  "title": "1. Basic Crawlability",
                  "description": "The baseline signals that decide whether AI crawlers can reach and index your pages at all. If these fail, nothing else matters.",
                  "items": {
                    "https": {
                      "name": "HTTPS",
                      "measures": "Checks whether your site is served over HTTPS and the TLS certificate is valid.",
                      "why": "AI engine crawlers (OpenAI GPTBot, Anthropic ClaudeBot, Perplexity, Google-Extended) skip non-HTTPS sites. Plain HTTP pages are downranked or ignored outright during both training and real-time retrieval.",
                      "scoring": "HTTPS with a valid certificate → PASS. HTTP that redirects to HTTPS is acceptable. Plain HTTP or an expired certificate → FAIL."
                    },
                    "robots": {
                      "name": "robots.txt crawler rules",
                      "measures": "Checks whether robots.txt exists, parses cleanly, and explicitly allows the major AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended, CCBot).",
                      "why": "AI crawlers honor robots.txt by default. A single Disallow: / or a User-agent: GPTBot block makes you invisible to OpenAI — the equivalent of voluntarily disappearing from the AI view of the web.",
                      "scoring": "File present and all major AI crawlers allowed → PASS. Blocking one or two minor crawlers → WARN. Blocking any of GPTBot, ClaudeBot, or CCBot → FAIL."
                    },
                    "sitemap": {
                      "name": "sitemap.xml",
                      "measures": "Checks whether sitemap.xml exists, is well-formed, contains a healthy number of URLs, and is referenced from robots.txt.",
                      "why": "AI crawlers discover pages through the sitemap rather than link-walking from the homepage. Without one, a crawler may grab only your index page and leave — your deep content stays invisible to LLMs.",
                      "scoring": "File present and URL count looks right → PASS. Present but very small or malformed → WARN. Missing → FAIL."
                    },
                    "llms": {
                      "name": "llms.txt",
                      "measures": "Checks whether llms.txt exists at the site root. This is a 2024 proposal — a Markdown file that tells LLMs what your site is and where its canonical content lives.",
                      "why": "llms.txt gives AI crawlers a curated tour instead of forcing them to guess which pages matter. Anthropic, Perplexity and others have started to support or reference the format, and early movers own the slot.",
                      "scoring": "File present → PASS. Missing → INFO (not yet mandatory, but cheap to add and first-mover advantage is real)."
                    },
                    "aiCrawlerAccess": {
                      "name": "AI crawler live accessibility",
                      "measures": "Sends real HTTP requests using GPTBot, ClaudeBot, PerplexityBot and other user-agents to see whether your WAF, Cloudflare Bot Fight, or CAPTCHA challenges block them.",
                      "why": "Allowing a bot in robots.txt is only half the story — Cloudflare Bot Fight or AWS WAF rules often block every non-browser user-agent as malicious, trapping legitimate AI crawlers with the rest. Robots policy and edge policy must agree.",
                      "scoring": "All AI user-agents return 200 → PASS. Some blocked → WARN. All blocked → FAIL."
                    }
                  }
                },
                "structuredData": {
                  "title": "2. Structured Data",
                  "description": "Machine-readable signals that let large language models verify what you are, what you sell, and how the page is organized — without guessing from prose.",
                  "items": {
                    "jsonld": {
                      "name": "JSON-LD Schema Markup",
                      "measures": "Checks for JSON-LD blocks on the homepage and whether they describe an Organization, Product, WebSite, or similar schema.org type, with the key fields filled in.",
                      "why": "Structured data is the most machine-readable signal you can give an LLM. ChatGPT, Perplexity, and Google AI Overviews all rely on schema.org markup to verify entity identity and extract facts — without it you force models to infer everything from prose, which is error-prone and gets hedged.",
                      "scoring": "Organization + one of {Product, WebSite, FAQ} blocks present with core fields → PASS. A single thin block → WARN. No JSON-LD → FAIL."
                    },
                    "metaTags": {
                      "name": "Meta Tag Coverage",
                      "measures": "Checks the homepage for title, meta description, canonical, viewport, Open Graph (og:title / og:description / og:image), and Twitter card tags.",
                      "why": "Meta tags are the 2-line summary AI uses when deciding whether a page is relevant. A missing description forces models to generate one themselves — or skip the page entirely. og:image is what AI-powered link previews render in chat answers.",
                      "scoring": "All 6 core signals present → PASS. 4–5 present → WARN. Fewer than 4 → FAIL."
                    },
                    "breadcrumbs": {
                      "name": "Breadcrumbs & Knowledge Panel Markup",
                      "measures": "Looks for BreadcrumbList JSON-LD, visible breadcrumb navigation, and knowledge-panel-friendly markup such as `sameAs`, `logo`, and `SearchAction`.",
                      "why": "Breadcrumbs help AI models understand your site hierarchy — where a page sits inside the category tree. Knowledge panel markup is what Google and Perplexity use to construct the summary box next to a query. Both compound over time.",
                      "scoring": "Both signals present → PASS. One present → WARN. Neither → FAIL."
                    },
                    "answerFormat": {
                      "name": "AI Answer Format Optimization",
                      "measures": "Checks whether content is written in a format LLMs can quote cleanly — FAQ schema, Q&A patterns, definition-style opening paragraphs, short direct answers.",
                      "why": "LLMs prefer to cite sources that give them a quotable, self-contained sentence. `\"X is a [category] that [value prop]\"` is vastly more citable than a marketing paragraph burying the answer. FAQ schema explicitly flags Q&A pairs for extraction.",
                      "scoring": "FAQ schema + clear definition openings → PASS. Partial → WARN. No quotable structure → FAIL."
                    }
                  }
                },
                "authority": {
                  "title": "3. Authority Signals",
                  "description": "The off-page evidence LLMs rely on to decide whether you are a real entity worth recommending — presence in training corpora, encyclopedias, review platforms, and the press.",
                  "items": {
                    "commonCrawl": {
                      "name": "AI Training Data Indexing (Common Crawl)",
                      "measures": "Looks up your domain in the latest Common Crawl index snapshot and counts how many of your pages have been captured into this public web corpus.",
                      "why": "Common Crawl is training data for ChatGPT, Claude, LLaMA and almost every open LLM. If you are not in Common Crawl, these models never saw you during training — they have zero memory of your brand when users ask and will hedge or omit you entirely.",
                      "scoring": "Pages found → PASS. Not found but domain is under 60 days old → INFO (normal for new sites). Not found on an older site → WARN. Not found and CCBot is blocked in robots.txt → FAIL."
                    },
                    "wikipedia": {
                      "name": "Wikipedia / Wikidata Entity",
                      "measures": "Checks whether your brand or product has an entry on Wikipedia (English and/or Chinese) and a Wikidata Q-item.",
                      "why": "Wikipedia and Wikidata are the single most authoritative structured sources LLMs use to verify entities. If ChatGPT can look you up on Wikidata, it treats you as a real entity; if not, you get the `I'm not sure about this brand` hedge that kills recommendations.",
                      "scoring": "Wikipedia article + Wikidata Q-item → PASS. One of the two → WARN. Neither → FAIL."
                    },
                    "knowledgeGraph": {
                      "name": "Google Knowledge Graph Presence",
                      "measures": "Checks for a Google Knowledge Graph entity — the sidebar box that shows up in Google results for recognized brands — via schema.org markup and Google's Knowledge Graph API.",
                      "why": "Google's Knowledge Graph feeds directly into Google AI Overviews, SGE, and Gemini. An entity that is in the graph gets cited; one that is not, does not. It also seeds the brand fact table for every other LLM that crawls Google results as a reference.",
                      "scoring": "Entity found with rich fields → PASS. Partial coverage → WARN. Not found → FAIL."
                    },
                    "reviews": {
                      "name": "Third-party Reviews & Ratings",
                      "measures": "Checks for a brand presence on the review platforms that matter in your category — G2, Capterra, Trustpilot, Glassdoor, Yelp, TripAdvisor, CNET, Product Hunt, and similar.",
                      "why": "AI engines weigh user reviews heavily when comparing competitors. A brand with 100 reviews at 4.5 stars on G2 gets recommended; a brand with none gets skipped in favor of one that has been vetted by real users. The platforms LLMs read are different per category.",
                      "scoring": "Presence on 3+ category-relevant platforms → PASS. 1–2 platforms → WARN. None → FAIL."
                    },
                    "mentions": {
                      "name": "Authoritative Press & Media Mentions",
                      "measures": "Searches for mentions of your brand on high-authority news and trade publications — WSJ, NYT, TechCrunch, Forbes, Bloomberg, industry analyst reports.",
                      "why": "Authority compounds. One TechCrunch article is worth more than a hundred backlinks from random blogs. LLMs trained on news datasets (GDELT, CCNews, RSS dumps) treat these mentions as ground truth when building a brand's entity profile.",
                      "scoring": "3+ high-authority mentions → PASS. 1–2 → WARN. None → FAIL."
                    }
                  }
                },
                "visibility": {
                  "title": "4. Direct AI Visibility",
                  "description": "The lagging-indicator metrics that measure whether real AI engines actually name and cite you when users ask questions in your category. Everything above is a leading indicator; this is what actually matters.",
                  "items": {
                    "citationRate": {
                      "name": "AI Citation Rate",
                      "measures": "Sends a fixed set of brand- and category-relevant questions to Perplexity (via OpenRouter) and counts how often your domain appears as a cited source.",
                      "why": "The ultimate test: are real AI engines pointing to you when users ask questions in your category? Everything else in this glossary is a leading indicator; citation rate is the lagging indicator that actually moves revenue.",
                      "scoring": "≥80% citation rate → A (excellent). 60–79% → B. 40–59% → C. 20–39% → D. <20% → F."
                    },
                    "answerInclusion": {
                      "name": "Answer Inclusion Rate",
                      "measures": "Similar to citation rate but softer — checks whether your brand name appears in the AI-generated answer text, even without a clickable citation link.",
                      "why": "Being named without a citation still earns mindshare. Users who read `Brands like X, Y, Z all do this` remember the names even without clicking. Answer inclusion is a leading indicator of citation rate — mentions come first, citations follow.",
                      "scoring": "≥60% of relevant queries name your brand → PASS. 30–59% → WARN. <30% → FAIL."
                    },
                    "shareOfVoice": {
                      "name": "Competitor Share of Voice",
                      "measures": "Across the same set of category queries, counts which competitor brands are mentioned alongside yours and how often.",
                      "why": "You don't just want to be mentioned — you want to be mentioned before your main competitors and more often than them. This metric shows whether AI perceives you as the category leader, a challenger, or an also-ran.",
                      "scoring": "Your brand in the top-3 most-mentioned → PASS. In top-10 → WARN. Not mentioned → FAIL."
                    },
                    "sentimentFraming": {
                      "name": "Brand Sentiment & Framing",
                      "measures": "Analyzes the emotional tone and narrative framing AI uses when describing your brand — innovator, challenger, niche player, has-had-issues, controversial.",
                      "why": "The frame AI picks up during training sticks for years. A brand framed as `innovator` in training data gets recommended proactively; a brand framed as `has had issues` gets hedged with disclaimers even when the situation has improved long ago.",
                      "scoring": "≥60% positive or neutral framing → PASS. 30–59% → WARN. <30% or frequent hedging → FAIL."
                    },
                    "contentGaps": {
                      "name": "Content Gaps",
                      "measures": "Identifies questions in your category where AI engines cannot find a good answer from your site — topics where a competitor wrote the definitive piece and you did not.",
                      "why": "Every unfilled content gap is a user journey your competitor owns. Filling the gap is the single most direct way to move citation rate, because AI will immediately start pointing to the new page once it's crawled.",
                      "scoring": "0 major gaps → PASS. 1–3 gaps → WARN. 4+ gaps → FAIL."
                    }
                  }
                },
                "entity": {
                  "title": "5. Entity Recognition",
                  "description": "Whether large language models perceive your brand as a real, distinct entity they can confidently describe — the foundation that everything else in AI visibility rests on.",
                  "items": {
                    "entityClarity": {
                      "name": "Entity Clarity",
                      "measures": "Asks AI models to describe your brand, then scores how accurate, specific, and complete the description is. Does the model know what you do, for whom, and how you're different?",
                      "why": "If AI can't crisply describe what you are, it can't recommend you. `I think they do something with AI` is a failure state — users re-ask and competitors jump the queue while the model is hedging on you.",
                      "scoring": "Accurate and specific description → PASS. Vague or partially wrong → WARN. Confused or unknown → FAIL."
                    },
                    "categoryAssociation": {
                      "name": "Category Association",
                      "measures": "Checks whether AI places your brand in the right mental bucket when users ask category questions. Ask `best tools for X` — do you appear? Asked about the wrong category — are you absent?",
                      "why": "Most buyer searches go through category intent. If AI classifies you in the wrong category (or none at all) you're invisible to people who are actively shopping — they'll never see your name.",
                      "scoring": "Correctly placed in the right category for ≥70% of queries → PASS. 30–69% → WARN. <30% → FAIL."
                    },
                    "platformCoverage": {
                      "name": "Multi-platform Presence",
                      "measures": "Checks whether your brand has a verified presence on the platforms AI models train on most heavily: Wikipedia, Wikidata, Crunchbase, LinkedIn, GitHub, Reddit, Product Hunt, Hacker News, industry directories.",
                      "why": "Every additional high-authority platform is another corroborating source that AI uses to build your entity profile. Brands with 6+ platform presences get recommended confidently; those with only a website get treated as unverified and hedged.",
                      "scoring": "Presence on ≥6 of 10 key platforms → PASS. 3–5 → WARN. <3 → FAIL."
                    },
                    "recognitionRate": {
                      "name": "Recognition Rate",
                      "measures": "The share of AI queries — across multiple engines and prompt variations — where the model recognizes your brand name without needing a URL or disambiguation.",
                      "why": "Recognition is the precursor to recommendation. If AI has to ask `which X do you mean?` every time, you lose the user to a brand the model already knows by name.",
                      "scoring": "≥80% recognition → PASS. 50–79% → WARN. <50% → FAIL."
                    },
                    "stability": {
                      "name": "Answer Stability",
                      "measures": "Runs the same query multiple times against the same AI engine and checks whether the answer is consistent across runs. Unstable answers signal weak training grounding.",
                      "why": "If the same question gives `X is a great tool` one time and `I've never heard of X` the next, users will trust the uncertain run and pass. Stability equals trust, and trust equals the recommendation.",
                      "scoring": "≥90% consistent answers → PASS. 70–89% → WARN. <70% → FAIL."
                    }
                  }
                }
              }
            }
          },
          "productsServices": {
            "title": "Products & Services",
            "description": "We offer a range of GEO detection and optimization services to help your website achieve optimal search visibility worldwide.",
            "sections": {
              "ourServices": "Our Services",
              "contactConsultation": "Contact & Consultation"
            },
            "cta": {
              "tryNow": "Try Now",
              "subscribeNow": "Subscribe",
              "contactSales": "Contact Sales",
              "popular": "Popular"
            },
            "cards": {
              "free": {
                "name": "Registered",
                "description": "Sign up to try GEO basic checks — great for individuals getting started",
                "period": "/mo",
                "features": [
                  "Account registration required",
                  "5 basic checks (17 sub-checks)",
                  "3 checks per month",
                  "Instant results"
                ]
              },
              "detector": {
                "name": "Detector",
                "description": "Full self-service checks for webmasters and indie developers",
                "period": "/mo",
                "features": [
                  "Account login required",
                  "23 complete checks (all sub-items)",
                  "20 checks per month",
                  "Check item priority sorting",
                  "Complete 23-category report (AI Visibility Score + letter grade + visualization)"
                ]
              },
              "starter": {
                "name": "Starter",
                "period": "/mo",
                "description": "Unlimited checks plus optimization suggestions for early-stage teams",
                "features": [
                  "Unlimited checks",
                  "23 complete checks + detailed optimization suggestions",
                  "Basic GEO coverage",
                  "OpenAI / Gemini / Anthropic indexing standards",
                  "Website copy creation and optimization"
                ]
              },
              "growth": {
                "name": "Growth",
                "period": "/mo",
                "description": "Unlimited checks + optimization + paid ranking placement for growth-stage brands",
                "features": [
                  "Unlimited checks",
                  "23 complete checks + detailed optimization suggestions",
                  "Overseas mainstream LLM indexing standard adaptation",
                  "Usage scenario-level copy and compliance configuration",
                  "Paid list SEO low-cost placement"
                ]
              },
              "scale": {
                "name": "Scale",
                "period": "",
                "description": "Full-channel GEO solution + PR for large enterprises — contact sales for a custom plan",
                "getDemoPrice": "Custom",
                "features": [
                  "Unlimited checks + detailed optimization suggestions",
                  "Full-channel customized GEO coverage",
                  "Full-channel overseas LLM indexing rules",
                  "Reputation management: 3-5 ranking articles per month + backlinks",
                  "PR support (media coverage outreach)"
                ]
              }
            },
            "loadingMemberships": "Loading plans…",
            "perProject": "starting / project",
            "selectPlaceholder": "Select a plan",
            "submitting": "Submitting…",
            "submitError": "Submission failed, please try again later",
            "closeAria": "Close",
            "table": {
              "headers": {
                "number": "#",
                "feature": "Features",
                "free": "Registered",
                "detector": "Detector",
                "starter": "Starter",
                "growth": "Growth",
                "scale": "Scale"
              },
              "rows": {
                "price": "Price",
                "type": "Type",
                "loginRequired": "Login Required",
                "checkItems": "Number of Check Items",
                "subCheckItems": "Number of Sub-check Items",
                "monthlyChecks": "Monthly Checks",
                "optimizationDetails": "Optimization Suggestions Detail",
                "prioritySorting": "Check Item Priority Sorting",
                "fullReport": "Full Report (23 Categories)",
                "history": "Check History",
                "support": "Technical Support",
                "basicGeo": "Basic GEO Coverage",
                "llmStandards": "Overseas LLM Indexing Standards",
                "websiteCopy": "Website Copy Creation & Optimization",
                "productInfo": "Core Product Information Compliance",
                "maintenance": "Ongoing Maintenance",
                "seoPlacement": "Paid List SEO Placement",
                "reputation": "Reputation Management",
                "prSupport": "PR Support",
                "serviceCycle": "Service Cycle / Output Rhythm"
              },
              "values": {
                "free": {
                  "price": "$0/mo",
                  "type": "Self-service SaaS",
                  "loginRequired": "✅ Yes",
                  "checkItems": "5 items",
                  "subCheckItems": "17 items",
                  "monthlyChecks": "3",
                  "optimizationDetails": "❌",
                  "prioritySorting": "❌",
                  "fullReport": "❌",
                  "history": "❌",
                  "support": "❌",
                  "basicGeo": "❌",
                  "llmStandards": "❌",
                  "websiteCopy": "❌",
                  "productInfo": "❌",
                  "maintenance": "❌",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "❌"
                },
                "detector": {
                  "price": "$9.99/mo",
                  "type": "Self-service SaaS",
                  "loginRequired": "✅ Yes",
                  "checkItems": "23 items",
                  "subCheckItems": "All items",
                  "monthlyChecks": "20",
                  "optimizationDetails": "❌",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "✅ Basic",
                  "basicGeo": "❌",
                  "llmStandards": "❌",
                  "websiteCopy": "❌",
                  "productInfo": "❌",
                  "maintenance": "❌",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "❌"
                },
                "starter": {
                  "price": "$999/mo",
                  "type": "SaaS subscription",
                  "loginRequired": "✅ Yes",
                  "checkItems": "23 items",
                  "subCheckItems": "All items",
                  "monthlyChecks": "Unlimited",
                  "optimizationDetails": "✅ Detailed suggestions",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "Basic Ticket",
                  "basicGeo": "✅ Basic",
                  "llmStandards": "✅ Completed",
                  "websiteCopy": "✅ Comprehensive",
                  "productInfo": "✅",
                  "maintenance": "✅ Ongoing",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "Monthly subscription"
                },
                "growth": {
                  "price": "$2,500/mo",
                  "type": "SaaS subscription",
                  "loginRequired": "✅ Yes",
                  "checkItems": "23 items",
                  "subCheckItems": "All items",
                  "monthlyChecks": "Unlimited",
                  "optimizationDetails": "✅ Detailed suggestions",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "Priority Response < 24h",
                  "basicGeo": "✅ Basic",
                  "llmStandards": "✅ Adapted",
                  "websiteCopy": "✅ With Usage Scenarios",
                  "productInfo": "✅ Usage Scenario Level",
                  "maintenance": "✅ Ongoing",
                  "seoPlacement": "✅ Low-cost Placement",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "Monthly subscription"
                },
                "scale": {
                  "price": "Custom",
                  "type": "Managed service",
                  "loginRequired": "✅ Yes",
                  "checkItems": "23 items",
                  "subCheckItems": "All items",
                  "monthlyChecks": "Unlimited",
                  "optimizationDetails": "✅ Detailed suggestions",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "24/7 + Dedicated Consultant",
                  "basicGeo": "✅ Full-channel Customization",
                  "llmStandards": "✅ Full-channel",
                  "websiteCopy": "✅ Customized Transaction Scenarios",
                  "productInfo": "✅ Customization + Verification",
                  "maintenance": "✅ Long-term",
                  "seoPlacement": "✅ Cost-effectiveness Monitoring",
                  "reputation": "✅ 3–5 articles/month + Backlinks",
                  "prSupport": "✅ Media Coverage",
                  "serviceCycle": "Monthly Continuous Output"
                }
              }
            },
            "services": [
              {
                "title": "Starter",
                "description": "Basic GEO Coverage: $2,000–$3,000\nPaid Listings: Complete indexing standards for major overseas models (OpenAI/Gemini/Anthropic), website copy creation and optimization, core product information compliance configuration and ongoing maintenance\nBest List SEO: None (not included)\nReputation Management: None (not included)\nPR: None (not included)",
                "price": "$2,000–$3,000",
                "features": [
                  "Basic GEO Coverage",
                  "Complete indexing standards for major overseas models",
                  "Website copy creation and optimization",
                  "Core product information compliance configuration and maintenance"
                ],
                "button": "Learn More"
              },
              {
                "title": "Growth",
                "description": "Basic GEO Coverage: $4,000–$7,000\nPaid Listings: Adapt to indexing standards for major overseas models (OpenAI/Gemini/Anthropic), website copy creation and optimization, core product usage scenario information compliance configuration and ongoing maintenance\nBest List SEO: Purchase low-cost placement on ranking websites\nReputation Management: Create and publish 1–2 top list articles (e.g., \"2026 Best XX Platform\"), published at the start of the campaign and optimized for large language model retrieval and citation\nPR: None (not included)",
                "price": "$4,000–$7,000",
                "features": [
                  "Basic GEO Coverage",
                  "Adapt to indexing standards for major overseas models",
                  "Purchase low-cost placement on ranking websites",
                  "Create and publish 1–2 top list articles"
                ],
                "button": "Learn More"
              },
              {
                "title": "Scale",
                "description": "Basic GEO Coverage: $8,000–$12,000\nPaid Listings: Full-channel overseas model GEO indexing rules, customized completion of transaction product core usage scenarios, information verification and ongoing maintenance\nBest List SEO: Monitor placement costs on ranking websites, determine the most cost-effective placement budget\nReputation Management: Write and publish 3-5 best list articles monthly, with supporting data articles, backlinks and verification page support\nPR: Can cooperate with internal corporate teams or dedicated PR agencies to help obtain media coverage",
                "price": "$8,000–$12,000",
                "features": [
                  "Full-channel overseas model GEO indexing rules",
                  "Monitor placement costs on ranking websites",
                  "Write and publish 3-5 best list articles monthly",
                  "Can cooperate with internal teams or PR agencies"
                ],
                "button": "Learn More"
              }
            ],
            "contact": {
              "getCustomPlan": "Get Custom Plan",
              "name": "Name",
              "namePlaceholder": "Your name",
              "email": "Email",
              "emailPlaceholder": "Your email",
              "website": "Website",
              "websitePlaceholder": "moltspay.com",
              "service": "Interested Service",
              "serviceOptions": [
                "Basic Detection Service",
                "Advanced Detection Service",
                "Custom GEO Optimization Service"
              ],
              "message": "Message",
              "messagePlaceholder": "Tell us your needs",
              "submit": "Submit Inquiry",
              "contactUs": "Contact Us",
              "contactText": "If you have any questions or need more information, please contact us through the following methods. Our professional team will reply to you as soon as possible.",
              "emailLabel": "Email",
              "phoneLabel": "Phone"
            }
          },
          "account": {
            "layout": {
              "subtitle": "Account",
              "logout": "Sign out"
            },
            "menu": {
              "profile": "Profile",
              "membership": "Membership",
              "usage": "Usage",
              "history": "Detection history"
            },
            "common": {
              "needLogin": "Please sign in first"
            },
            "profile": {
              "accountInfo": "Account information",
              "email": "Email",
              "status": "Status",
              "active": "Active",
              "inactive": "Disabled",
              "userId": "User ID",
              "changePassword": "Change password",
              "oldPassword": "Current password",
              "newPassword": "New password",
              "confirmPassword": "Confirm new password",
              "submit": "Update password",
              "submitting": "Submitting...",
              "success": "Password updated successfully",
              "failed": "Password update failed",
              "minLength": "New password must be at least 6 characters",
              "mismatch": "New passwords do not match"
            },
            "membership": {
              "currentPlan": "Current plan",
              "service": "Managed service",
              "contactSales": "Contact sales",
              "startDate": "Start date",
              "endDate": "End date",
              "permanent": "Lifetime",
              "daysLeft": "Days left",
              "days": "days",
              "upgrade": "Upgrade plan",
              "browse": "Browse plans",
              "renew": "Renew",
              "cancel": "Cancel membership",
              "cancelConfirm": "Are you sure you want to cancel your membership?",
              "renewHint": "Payment will be available soon. Please contact support.",
              "features": "What's included"
            },
            "usage": {
              "monthlyUsage": "This month's usage",
              "times": "checks",
              "unlimited": "Your plan has no monthly check limit.",
              "remaining": "{{n}} left. Resets at the start of next month.",
              "unlockedCategories": "Unlocked categories",
              "allUnlocked": "All 23 detection categories unlocked.",
              "partialUnlocked": "Upgrade your plan to unlock more categories."
            },
            "history": {
              "title": "Detection history",
              "total": "{{n}} records",
              "empty": "No detection records yet",
              "goCheck": "Run a check",
              "time": "Time",
              "url": "URL",
              "score": "Score",
              "mode": "Mode",
              "modeFree": "Standard",
              "modeAdvanced": "Advanced",
              "actions": "Actions",
              "view": "View",
              "loading": "Loading...",
              "delete": "Delete",
              "deleteConfirm": "Delete this detection record?",
              "prev": "Prev",
              "next": "Next",
              "pageOf": "Page {{page}} of {{pages}}"
            }
          }
        }
      },
      zh: {
        translation: {
          "nav": {
            "home": "首页",
            "geoKnowledge": "GEO 知识",
            "productsServices": "产品与服务",
            "aboutUs": "关于我们",
            "about": "关于 GEO",
            "process": "服务流程",
            "pricing": "定价方案",
            "data": "数据洞察",
            "contact": "联系我们",
            "langSwitch": "EN",
            "login": "登录",
            "register": "注册",
            "signedInAs": "已登录为",
            "account": "账户中心",
            "logout": "退出登录"
          },
          "common": {
            "error": "错误",
            "loading": "加载中...",
            "contact": "登录",
            "cancel": "退出",
            "success": "成功",
            "theme": {
              "switchToLight": "切换到亮色模式",
              "switchToDark": "切换到暗色模式"
            },
            "errors": {
              "loadFailed": "加载失败",
              "loadFailedWith": "加载{{entity}}失败",
              "deleteFailed": "删除失败",
              "paymentFailed": "支付失败",
              "genericFailed": "操作失败"
            }
          },
          "hero": {
            "title": "让 AI 推荐您的品牌",
            "subtitle": "生成式引擎优化（GEO）",
            "description": "当潜在客户向 ChatGPT、Gemini、Perplexity 等 AI 助手寻求产品或服务推荐时，确保您的企业被优先推荐。",
            "cta": "了解更多",
            "ctaSecondary": "联系我们"
          },
          "about": {
            "sectionTag": "关于 GEO",
            "title": "什么是生成式引擎优化？",
            "description": "GEO（Generative Engine Optimization）是让企业在生成式 AI 聊天机器人中获得推荐的过程。一种针对生成式人工智能搜索引擎（如 ChatGPT、Perplexity AI、Gemini、DeepSeek Search 以及未来百度、谷歌的 AI 搜索）进行内容优化的新兴技术。",
            "definition": "GEO 定义",
            "definitionText": "GEO 是让您的公司在潜在客户向生成式 AI 聊天机器人寻求产品或服务推荐时被建议的过程，这一过程可以针对特定的生成式 AI 搜索引擎进行定制，如 ChatGPT 优化和 Perplexity 优化。",
            "combination": "三位一体",
            "combinationText": "GEO 服务是 SEO、PR 和声誉管理的结合。理想的 GEO 服务方案将以下活动作为核心策略：超级列表排名、网站 SEO 优化、评论声誉管理和公关传播。",
            "difference": "GEO vs 传统 SEO",
            "differenceText": "传统 SEO 专注于让您的网站在搜索引擎结果中排名靠前，而 GEO 专注于让您的品牌在 AI 生成的内容中被推荐。随着 AI 搜索的兴起，GEO 正成为数字营销不可或缺的组成部分。"
          },
          "process": {
            "sectionTag": "服务流程",
            "title": "GEO 四步核心策略",
            "subtitle": "我们的 GEO 服务涵盖以下四个核心步骤，确保您的品牌在 AI 时代获得最大曝光。",
            "step1": {
              "title": "超级列表排名",
              "description": "在 Google 排名靠前的超级列表中获取高排名位置。例如，对于车队管理软件公司，在「2025 年最佳车队管理软件」列表文章中获取顶级位置。可通过 PR、付费投放或自行发布超级列表文章并将公司排名第一来实现。"
            },
            "step2": {
              "title": "网站 SEO 优化",
              "description": "对您的网站进行 SEO 优化，使列表文章在搜索结果中排名靠前。如果您为每个产品或服务类别发布列表文章，且它们在搜索结果中排名前三，您被 ChatGPT、Gemini 和 Perplexity 推荐的可能性将大大增加。"
            },
            "step3": {
              "title": "评论声誉管理",
              "description": "确保在最热门的评论网站上获得好评。对于技术服务，这些网站包括 G2 和 Clutch；对于软件，Capterra 和 PC Magazine；对于旅游和休闲业务，TripAdvisor 和 Yelp；对于消费科技，CNet 和 Consumer Reports。B2B 企业还应关注 Glassdoor 和 Indeed 等员工评论网站。"
            },
            "step4": {
              "title": "公关传播",
              "description": "通过传统 PR 生成正面宣传。如果一家公司在华尔街日报和纽约时报等权威出版物中获得赞誉，生成式 AI 聊天机器人更有可能推荐该公司。"
            }
          },
          "pricing": {
            "sectionTag": "定价方案",
            "title": "选择适合您的 GEO 方案",
            "subtitle": "我们提供三个层级的 GEO 服务，满足不同规模企业的需求。",
            "tier1": {
              "name": "基础版",
              "price": "¥14,000",
              "priceEnd": "–¥21,000",
              "period": "/月",
              "description": "适合刚开始探索 GEO 的企业",
              "features": [
                "购买低成本排名网站位置",
                "基础列表投放服务",
                "月度效果报告"
              ],
              "notIncluded": [
                "超级列表 SEO",
                "声誉管理",
                "公关传播"
              ]
            },
            "tier2": {
              "name": "专业版",
              "price": "¥28,000",
              "priceEnd": "–¥49,000",
              "period": "/月",
              "description": "适合希望系统化推进 GEO 的企业",
              "features": [
                "监控排名网站投放成本，确定最具影响力的投放支出",
                "活动初期创建 1-2 篇超级列表文章",
                "月度效果报告与策略调整",
                "关键词排名追踪"
              ],
              "notIncluded": [
                "声誉管理",
                "公关传播"
              ]
            },
            "tier3": {
              "name": "旗舰版",
              "price": "¥56,000",
              "priceEnd": "–¥84,000",
              "period": "/月",
              "description": "全方位 GEO 服务，最大化 AI 推荐效果",
              "features": [
                "监控排名网站投放成本，确定最具影响力的投放支出",
                "每月创建 3-5 篇超级列表文章，配合指标文章、权威声明和验证页面",
                "促进客户正面评价，减少负面评价影响",
                "确保主要行业目录描述反映行业领导地位",
                "与内部团队或专业 PR 机构合作获取媒体报道",
                "专属策略顾问"
              ],
              "notIncluded": []
            },
            "cta": "开始咨询"
          },
          "data": {
            "sectionTag": "数据洞察",
            "title": "GEO 关键数据",
            "subtitle": "了解生成式 AI 搜索市场的最新趋势和 GEO 的影响力。",
            "stat1": {
              "value": "65%",
              "label": "用户使用 AI 搜索产品推荐"
            },
            "stat2": {
              "value": "3x",
              "label": "GEO 优化后品牌推荐率提升"
            },
            "stat3": {
              "value": "5+",
              "label": "覆盖主流 AI 平台"
            },
            "stat4": {
              "value": "89%",
              "label": "客户满意度"
            },
            "platforms": {
              "title": "我们覆盖的 AI 平台"
            }
          },
          "contact": {
            "sectionTag": "联系我们",
            "title": "开启您的 GEO 之旅",
            "subtitle": "填写以下信息，我们的 GEO 专家将在 24 小时内与您联系。",
            "name": "姓名",
            "email": "邮箱",
            "company": "公司",
            "message": "留言",
            "submit": "提交咨询",
            "info": {
              "email": "geo@zen7.com",
              "phone": "+86 400-XXX-XXXX"
            },
            "form": {
              "name": "姓名",
              "email": "邮箱",
              "website": "网站",
              "message": "留言",
              "submit": "提交",
              "sending": "发送中...",
              "success": "您的留言已成功发送！我们将尽快与您联系。",
              "error": "发送留言时出现错误，请稍后再试。",
              "errors": {
                "name": "请输入您的姓名",
                "email": "请输入有效的邮箱地址",
                "website": "请输入有效的网站 URL",
                "message": "请输入您的留言"
              },
              "placeholders": {
                "name": "您的姓名",
                "email": "您的邮箱",
                "website": "您的网站",
                "message": "您的留言"
              }
            },
            "otherWays": "其他联系方式",
            "backToHome": "返回首页",
            "faq": {
              "title": "常见问题",
              "questions": {
                "question1": "什么是GEO，它与SEO有什么不同？",
                "question2": "GEO优化需要多长时间才能看到效果？",
                "question3": "你们为哪些AI平台进行优化？",
                "question4": "GEO优化的费用是多少？",
                "question5": "你们提供持续的GEO维护服务吗？"
              },
              "answers": {
                "answer1": "GEO（生成式引擎优化）专注于让您的品牌被ChatGPT、Gemini和Perplexity等AI聊天机器人推荐，而传统SEO专注于在搜索引擎结果中排名靠前。GEO是专门为AI驱动搜索的新时代设计的。",
                "answer2": "效果因行业和竞争而异，但大多数客户在1-3个月内开始看到改进。完全优化通常需要3-6个月才能看到最大效果。",
                "answer3": "我们为所有主要AI平台进行优化，包括ChatGPT、Gemini、Perplexity、DeepSeek以及未来Google和百度的AI驱动搜索。",
                "answer4": "我们的GEO服务根据项目范围和复杂性从每月¥14,000到¥84,000不等。我们提供定制计划以满足不同业务需求和预算。",
                "answer5": "是的，我们提供持续的GEO维护服务，确保您的品牌在AI算法演变时继续表现良好。我们的维护计划包括定期监控、更新和策略调整。"
              }
            }
          },
          "footer": {
            "description": "Zen7 是专业的生成式引擎优化（GEO）服务提供商，帮助企业在 AI 时代获得最大品牌曝光。",
            "quickLinks": "快速链接",
            "contactInfo": "联系方式",
            "copyright": "© 2026 Zen7. 保留所有权利。"
          },
          "geoTool": {
            "title": "想要检查您网站的 GEO 就绪状态？",
            "description": "使用我们的免费 GEO 检查器来分析您的网站。",
            "cta": "检查您的网站"
          },
          "home": {
            "title": "GEO 检测",
            "description": "优化您的网站以适应 AI 驱动的搜索引擎和助手。获取详细的见解和可操作的建议。",
            "placeholder": "moltspay.com",
            "button": "检测 GEO",
            "analyzing": "分析中...",
            "error": {
              "empty": "请输入 URL",
              "invalid": "请输入有效的 URL",
              "failed": "检测失败，请稍后重试",
              "quotaExceeded": "本月的免费检测次数已用完，升级会员方案后可继续检测。",
              "quotaCta": "查看会员方案"
            },
            "poweredBy": "由 GEO AI 提供支持",
            "contactLink": "需要专家优化帮助？",
            "advanced": {
              "badge": "会员专享",
              "title": "高级检测能力",
              "subtitle": "开通会员，解锁专业 GEO 检测工具，全面洞察 AI 引擎对你的网站的认知。",
              "upgrade": "升级解锁",
              "comingSoon": "功能即将上线",
              "tierModal": {
                "title": "选择你的会员等级",
                "subtitle": "开通会员套餐即可解锁高级检测能力。"
              },
              "validation": {
                "minUrls": "请至少输入 2 个要对比的 URL",
                "invalidUrl": "URL 格式无效：{{url}}",
                "entityRequired": "请输入实体名称",
                "unexpected": "发生未知错误，请稍后重试"
              },
              "cards": {
                "compare": {
                  "title": "竞争对比检测",
                  "desc": "多站点并排对比全部 GEO 类目的得分差距。"
                },
                "crawlTest": {
                  "title": "AI 爬虫测试",
                  "desc": "验证 GPTBot、ClaudeBot 等 AI 爬虫能否真正抓取你的页面。"
                },
                "authority": {
                  "title": "权威信号审计",
                  "desc": "评估外部评价、提及和 AI 信任的第三方信号。"
                },
                "citation": {
                  "title": "AI 引用检测",
                  "desc": "检测 AI 引擎在品牌相关问题中是否会引用你的网站。"
                },
                "visibility": {
                  "title": "AI 可见性审计",
                  "desc": "跨 Perplexity、ChatGPT、Claude 的多引擎可见性全面报告。"
                },
                "entity": {
                  "title": "实体 GEO 审计",
                  "desc": "无需 URL，审计 AI 对品牌/产品/人物的认知度。"
                }
              },
              "result": {
                "lead": "领先",
                "compare": {
                  "categoryCompare": "类目得分对比",
                  "sitesCategories": "{{sites}} 站点 · {{categories}} 类目",
                  "category": "类目",
                  "total": "总分"
                },
                "crawl": {
                  "targetDomain": "目标域名",
                  "ccFound": "Common Crawl 已收录",
                  "ccNotFound": "Common Crawl 未收录",
                  "totalIssues": "问题总数",
                  "allClear": "一切正常",
                  "needsFix": "需要修复",
                  "robotsAllowed": "robots 放行",
                  "crawlerPermission": "爬虫许可",
                  "wafAllowed": "WAF 放行",
                  "liveAccess": "实测访问",
                  "robotsTitle": "robots.txt 规则",
                  "detected": "已检测",
                  "notFound": "未找到",
                  "robotsMissingWarning": "未找到 robots.txt — 默认所有爬虫被允许",
                  "wafTitle": "WAF / CDN 实测",
                  "wafBaseline": "基线 {{status}} · {{size}}KB",
                  "commonCrawlTitle": "Common Crawl 索引",
                  "pagesSuffix": "{{count}} 页",
                  "notIndexed": "未收录",
                  "foundInCcPrefix": "已在 Common Crawl 中找到 ",
                  "foundInCcSuffix": " 页"
                },
                "citation": {
                  "cited": "已引用",
                  "queries": "查询数量",
                  "citationCount": "引用次数",
                  "directCitations": "直接引用",
                  "grade": "评级",
                  "overallScore": "综合评分",
                  "perQuery": "逐条查询结果",
                  "queriesSuffix": "{{count}} 条"
                },
                "visibility": {
                  "queryBreakdown": "{{count}} 个查询 × {{runs}} 次稳定性运行",
                  "perEngineRate": "各引擎可见率",
                  "competitors": "同时被提及的竞品",
                  "noCompetitors": "未提取到竞品。",
                  "framings": "品牌情感框架",
                  "contentGaps": "内容缺口",
                  "noGaps": "没有重大缺口"
                },
                "entity": {
                  "kgTitle": "知识图谱覆盖",
                  "platforms": "平台覆盖",
                  "sentimentTitle": "情感与框架",
                  "overallSentiment": "整体情感",
                  "bestFraming": "最佳框架",
                  "recognitionRate": "识别率",
                  "contentGaps": "内容缺口",
                  "noGaps": "未发现缺口"
                }
              }
            },
            "buttons": {
              "geoKnowledge": "了解 GEO 知识",
              "services": "查看服务套餐"
            }
          },
          "result": {
            "title": "GEO 检测结果",
            "resultsFor": "检查结果：",
            "scoreCard": {
              "title": "AI 可见性得分",
              "description": "您的网站对 AI 搜索的优化程度",
              "grade": "等级"
            },
            "summary": {
              "passed": "通过",
              "warnings": "警告",
              "failed": "失败",
              "info": "信息",
              "totalChecks": "总检查项"
            },
            "detailedResults": "详细结果",
            "fix": "修复建议：",
            "buttons": {
              "checkAnother": "返回",
              "getHelp": "获取优化帮助"
            },
            "error": {
              "noData": "未找到 GEO 检查数据。请先运行检查。"
            },
            "loginToView": "登录查看完整结果",
            "loginToViewDesc": "登录以查看所有详细结果和建议",
            "loginButton": "登录",
            "paywall": {
              "lockedCount": "还有 {{count}} 项检测结果已锁定",
              "subtitle": "开通会员即可解锁所有检测详情与优化建议",
              "viewAll": "查看全部 →",
              "perCategoryHint": "查看 {{total}} 项检查中的 2 项（会员可查看全部）"
            },
            "groupProgress": {
              "title": "分类进度"
            },
            "categories": {
              "infraProtocols": "基础协议与可抓取性",
              "pageBasics": "页面基础与移动体验",
              "aiProtocols": "AI 专属协议与抓取",
              "structuredSemantic": "结构化与语义",
              "contentQuality": "内容质量与可读性",
              "techRobustness": "技术健壮性与媒体",
              "authorityExternal": "权威与外部信号",
              "other": "其它"
            },
            "categoryLabels": {
              "HTTPS": "HTTPS 安全协议",
              "robots.txt": "robots.txt",
              "llms.txt": "llms.txt",
              ".well-known Discovery": ".well-known 发现",
              "sitemap.xml": "sitemap.xml 站点地图",
              "Search Engine & AI Platform Registration": "搜索引擎 / AI 平台收录",
              "Structured Data": "结构化数据（JSON-LD）",
              "Meta Tags": "Meta 标签",
              "Content Accessibility": "内容可读性",
              "AI Crawl Readiness": "AI 爬虫可访问性",
              "Content Quality for AI": "内容质量（面向 AI）",
              "Technical Crawlability": "技术抓取能力",
              "Authority & Trust Signals": "权威与信任信号",
              "AI-Specific Optimization": "AI 专项优化",
              "Social Signals": "社交信号",
              "AI Answer Format Optimization": "AI 答案格式优化",
              "Schema Breadcrumbs & Knowledge Panel": "Schema / 知识面板",
              "Mobile-Friendliness & Page Weight": "移动端友好性与页面体积",
              "URL Normalization": "URL 规范化",
              "Outbound Links & Media": "出站链接与媒体",
              "Multilingual Content Depth": "多语言内容深度",
              "Cross-Platform Content Distribution": "跨平台内容分发",
              "Multi-Page Sampling": "多页面采样"
            },
            "header": {
              "rerunPlaceholder": "输入新的 URL 重新检测",
              "rerun": "重新检测",
              "modeLabel": "检测模式",
              "modeDefault": "标准检测",
              "modeLockedHint": "升级会员解锁此模式",
              "placeholderCompare": "https://a.com, https://b.com, https://c.com",
              "placeholderKeywords": "关键词（可选，逗号分隔）",
              "placeholderEntity": "品牌 / 产品 / 人物 名称",
              "entityTypeLabel": "实体类型",
              "entityType": {
                "brand": "品牌",
                "product": "产品",
                "person": "人物"
              },
              "compareHint": "用逗号或空格分隔 2-5 个网址",
              "visibilityHint": "关键词留空则自动生成；多个关键词用逗号分隔",
              "entityHint": "输入要审计的品牌 / 产品 / 人物名称"
            },
            "visuals": {
              "robots": {
                "title": "AI 爬虫许可矩阵",
                "filePresent": "robots.txt · 文件存在",
                "fileMissing": "robots.txt · 文件缺失",
                "sitemapRef": "sitemap 引用 ✓",
                "noSitemapRef": "缺少 sitemap 引用",
                "wildcardWarning": "通配符规则 User-agent: * 禁止所有爬虫。下方标为「继承通配符」的机器人在你覆盖之前实际上都是被屏蔽的。",
                "legend": {
                  "allowed": "已允许",
                  "blocked": "已屏蔽",
                  "inherited": "继承通配符",
                  "unknown": "未知"
                }
              },
              "meta": {
                "title": "Meta 标签覆盖",
                "subtitle": "AI 引擎用于生成摘要的 6 个信号",
                "passCount": "{{pass}}/{{total}} 通过"
              },
              "platform": {
                "titleCross": "跨平台存在度",
                "titleSocial": "社交信号覆盖",
                "subtitle": "AI 引擎会训练的平台来源",
                "notDetected": "{{name}} 未检测到"
              }
            },
            "shareExport": {
              "title": "分享和导出",
              "copied": "链接已复制到剪贴板！",
              "copyLink": "复制链接",
              "exportPDF": "导出 PDF",
              "exportPDFLoading": "导出 PDF 中...",
              "exportCSV": "导出 CSV",
              "shareSocial": "分享到社交媒体",
              "share": "分享",
              "downloadReport": "下载报告",
              "downloadReportLoading": "正在生成报告…"
            },
            "pdfReport": {
              "title": "GEO 就绪度检测报告",
              "subtitle": "衡量网站在 AI 搜索引擎中的可见度与优化程度",
              "targetSite": "目标网站",
              "generatedAt": "生成时间",
              "tier": "报告等级",
              "overallScore": "综合得分",
              "scoreInterpretation": "得分解读",
              "scoreLevels": {
                "excellent": "优秀 — 网站对 AI 搜索引擎已有良好优化，建议保持监控并持续微调。",
                "good": "良好 — 基础要素基本到位，再修复几项重点问题即可进入顶级梯队。",
                "average": "一般 — 存在多项重要信号缺失，建议优先修复下方标红/警告项以提升可见度。",
                "poor": "待改进 — AI 引擎对您网站内容的呈现度不足，建议尽快处理失败项。",
                "critical": "严重不足 — 网站对 AI 引擎几乎不可见，建议进行一次完整的 GEO 优化。"
              },
              "groupSection": "分类得分概览",
              "groupLabel": "分类",
              "recommendationsSection": "优先修复建议",
              "topFixesIntro": "下列是对得分影响最大的问题，按严重程度排序，优先处理这些项收益最高。",
              "detailSection": "检测详情",
              "appendixSection": "关于本报告",
              "appendixBody": "本报告由 GEO Checker 生成，分数反映您的网站对 ChatGPT、Perplexity、Google AI Overviews、Copilot 等 AI 搜索引擎最佳实践的遵循程度。完成修复后可重新运行检测查看进展。",
              "footer": "GEO Checker · © {{year}} · 保留所有权利",
              "fileName": "GEO就绪度报告",
              "fixLabel": "修复建议",
              "noFix": "暂无具体修复建议。",
              "noFailItems": "未发现失败或警告项，状态良好！",
              "lockedNotice": "本报告中有 {{count}} 个分类分组处于锁定状态，升级会员可解锁完整结果。",
              "statusLabels": {
                "pass": "通过",
                "warn": "警告",
                "fail": "失败",
                "info": "信息"
              },
              "tierLabels": {
                "free": "免费版",
                "pro": "检测会员",
                "starter": "Starter",
                "growth": "Growth",
                "scale": "旗舰版"
              },
              "pageOf": "第 {{current}} 页 / 共 {{total}} 页",
              "coverBadge": "GEO 就绪度检测报告",
              "headerSite": "检测目标"
            }
          },
          "login": {
            "title": "登录",
            "subtitle": "欢迎回来！请登录您的账户",
            "createAccount": "创建新账号",
            "email": "邮箱",
            "emailPlaceholder": "请输入邮箱",
            "password": "密码",
            "passwordPlaceholder": "请输入密码",
            "rememberMe": "记住我",
            "forgotPassword": "忘记密码？",
            "button": "登录",
            "loading": "登录中...",
            "noAccount": "没有账户？",
            "register": "立即注册",
            "failed": "登录失败，请检查您的邮箱和密码",
            "error": "邮箱或密码错误"
          },
          "register": {
            "title": "注册账户",
            "subtitle": "加入我们，开始您的 GEO 优化之旅",
            "loginAccount": "登录现有账号",
            "name": "姓名",
            "namePlaceholder": "您的姓名",
            "email": "邮箱",
            "emailPlaceholder": "请输入邮箱",
            "password": "密码",
            "passwordPlaceholder": "请输入密码",
            "confirmPassword": "确认密码",
            "confirmPasswordPlaceholder": "请再次输入密码",
            "terms": "我同意服务条款和隐私政策",
            "termsRequired": "请同意服务条款和隐私政策",
            "button": "注册",
            "loading": "注册中...",
            "passwordMismatch": "两次输入的密码不一致",
            "failed": "注册失败",
            "hasAccount": "已有账户？",
            "login": "立即登录",
            "passwordHint": "密码长度至少 6 位",
            "error": "密码不匹配"
          },
          "forgotPassword": {
            "title": "重置密码",
            "subtitle": "请输入您的邮箱，我们将向您发送重置链接",
            "button": "发送重置链接",
            "loading": "发送中...",
            "success": "重置链接已发送到您的邮箱，请查收",
            "failed": "发送失败，请检查邮箱是否正确",
            "error": {
              "sendFailed": "发送密码重置邮件失败",
              "resetFailed": "重置密码失败"
            },
            "backToLogin": "返回登录",
            "reset": {
              "title": "重置密码",
              "description": "请输入您的新密码",
              "newPassword": "新密码",
              "newPasswordPlaceholder": "输入您的新密码",
              "confirmPassword": "确认密码",
              "confirmPasswordPlaceholder": "确认您的新密码",
              "button": "重置密码",
              "loading": "重置中..."
            }
          },
          "checkoutPending": {
            "title": "待支付订单",
            "subtitle": "请确认订单信息后完成支付",
            "loading": "加载订单中…",
            "missingSlug": "缺少套餐标识",
            "notFound": "未找到对应套餐",
            "notSubscribable": "该套餐不支持自助订阅，请联系销售。",
            "backToPlans": "返回套餐列表",
            "planLabel": "订阅套餐",
            "popular": "热门",
            "totalLabel": "应付金额",
            "methodLabel": "支付方式",
            "methodStripe": "点击后将跳转到 Stripe 安全完成支付。",
            "methodDomestic": "点击\"立即支付\"后将为您创建订单，微信 / 支付宝渠道即将上线。",
            "domesticPending": "微信 / 支付宝支付即将上线，请联系销售完成订阅。",
            "submitting": "正在跳转支付…",
            "payNow": "立即支付",
            "cancel": "取消",
            "payError": "支付发起失败"
          },
          "aboutUs": {
            "title": "关于我们",
            "description": "专注于GEO（生成式引擎优化）的技术服务提供商，帮助网站在生成式AI时代获得更好的表现。",
            "story": {
              "title": "我们的故事",
              "subtitle": "从技术创新到行业领先",
              "paragraph1": "我们的团队由一群对AI和搜索引擎技术充满热情的专家组成。在生成式AI技术爆发的时代，我们看到了网站优化的新机遇和挑战。",
              "paragraph2": "2023年，我们开始研发GEO（生成式引擎优化）技术，旨在帮助网站在生成式AI时代获得更好的表现。经过一年的努力，我们开发出了一套完整的GEO检测和优化系统，为企业和个人网站提供专业的技术支持。",
              "paragraph3": "今天，我们已经成为GEO领域的领先服务提供商，为众多客户提供了专业的GEO检测和优化服务，帮助他们在生成式AI时代获得更好的线上表现。"
            },
            "mission": {
              "title": "我们的使命",
              "innovation": {
                "title": "技术创新",
                "description": "不断探索和创新GEO技术，为客户提供最先进的检测和优化方案。"
              },
              "value": {
                "title": "客户价值",
                "description": "以客户需求为中心，提供高质量的GEO服务，帮助客户在生成式AI时代获得竞争优势。"
              },
              "leadership": {
                "title": "行业领先",
                "description": "成为GEO领域的行业领导者，推动行业标准的建立和发展。"
              }
            },
            "team": {
              "title": "我们的团队",
              "ceo": {
                "name": "张明",
                "title": "创始人 & CEO",
                "description": "前Google工程师，拥有10年搜索引擎和AI技术经验，专注于GEO技术的研发和应用。"
              },
              "cto": {
                "name": "李婷",
                "title": "技术总监",
                "description": "前微软研发工程师，拥有8年AI和机器学习经验，负责GEO检测系统的技术架构。"
              },
              "marketing": {
                "name": "王强",
                "title": "市场总监",
                "description": "前百度营销专家，拥有7年数字营销经验，负责公司的市场策略和客户拓展。"
              },
              "design": {
                "name": "赵芳",
                "title": "设计总监",
                "description": "前腾讯UI/UX设计师，拥有6年用户体验设计经验，负责产品的界面设计和用户体验。"
              }
            },
            "contact": {
              "title": "联系我们",
              "info": {
                "title": "联系方式",
                "address": "地址",
                "addressValue": "北京市海淀区中关村科技园区",
                "email": "邮箱",
                "emailValue": "contact@geochecker.com",
                "phone": "电话",
                "phoneValue": "+86 10 8888 8888"
              },
              "form": {
                "title": "发送消息",
                "name": "姓名",
                "namePlaceholder": "您的姓名",
                "email": "邮箱",
                "emailPlaceholder": "您的邮箱",
                "message": "消息",
                "messagePlaceholder": "请输入您的消息",
                "button": "发送消息"
              }
            }
          },
          "geoKnowledge": {
            "title": "GEO 知识中心",
            "description": "了解什么是生成式引擎优化（Generative Engine Optimization），它为何会在 AI 时代取代传统 SEO，以及如何让 ChatGPT、Perplexity、Google AI Overviews、Gemini、Claude、Copilot 等 AI 引擎优先推荐你的品牌。",
            "sections": {
              "about": "关于 GEO",
              "whatIsGeo": "什么是 GEO？",
              "whatIsGeoBody": "GEO 即 Generative Engine Optimization（生成式引擎优化），是一套针对 AI 搜索引擎和 AI 助手（ChatGPT、Perplexity、Google AI Overviews、Gemini、Claude、Copilot 等）的优化实践，目的是让 AI 在回答用户关于产品、服务或专家咨询的问题时，能够识别、信任并推荐你的品牌。传统 SEO 优化的是搜索结果页上的蓝色链接，而 GEO 优化的是 AI 直接生成的那段答案本身。",
              "whyGeoImportant": "为什么 GEO 很重要？",
              "whyGeoPoints": [
                "传统搜索流量正在被 AI 生成的答案取代——如果你不在答案里，就是彻底隐形",
                "AI 引擎每个查询只会引用极少量的可信来源，进入这个名单就锁定了品类心智",
                "一旦 AI 模型把你识别为品牌实体，它会在大量相关查询中反复推荐你，效果会复利放大",
                "GEO 的先行者能在竞争对手反应过来之前锁定品类权威地位"
              ],
              "strategies": "GEO 策略",
              "contentLocalization": "权威内容与实体清晰度",
              "contentLocalizationDesc": "AI 引擎只引用它能够验证的内容。围绕品牌、产品和所在品类建立深度第一方内容，提供清晰的实体信号，让大模型能够解析并信任。",
              "contentLocalizationPoints": [
                "围绕核心话题产出深度、专家级的原创内容，拒绝稀薄营销文案",
                "添加 Organization / Product 结构化数据（JSON-LD）与 llms.txt 文件",
                "在 Wikipedia、Wikidata、Reddit、GitHub 及权威目录站建立品牌存在"
              ],
              "technicalOptimization": "技术基础与 AI 可抓取性",
              "technicalOptimizationDesc": "AI 爬虫拿不到你的内容就无法引用你。确保 GPTBot、ClaudeBot、PerplexityBot、Google-Extended 能够真实抓取并渲染你的页面。",
              "technicalOptimizationPoints": [
                "在 robots.txt 中放行 AI 爬虫（GPTBot、ClaudeBot、PerplexityBot、Google-Extended）",
                "提供快速的服务端渲染 HTML，避免纯 JS 页面——AI 爬虫不会执行 JavaScript",
                "使用语义化 HTML 与干净的标记，让大模型能把页面解析成结构化事实"
              ],
              "keyData": "GEO 关键指标",
              "importantMetrics": "该衡量什么",
              "regionalTraffic": "AI 引用率",
              "regionalTrafficDesc": "当 AI 引擎回答品牌相关或品类相关问题时，把你的站点作为来源引用的频率。",
              "languagePreference": "答案提及率",
              "languagePreferenceDesc": "在相关查询中，你的品牌实际出现在 AI 生成答案里的比例——无论是否附带直接引用链接。",
              "searchTrends": "竞品声量占比",
              "searchTrendsDesc": "AI 模型会和你一起提到哪些竞品、出现频率如何——这才是品类心智的真实基准。"
            },
            "tabs": {
              "overview": "概览",
              "metrics": "指标词典"
            },
            "metrics": {
              "title": "GEO 检测指标词典",
              "description": "GEO 就绪度报告里的每一项检测——它在测什么、为什么对 AI 可见性重要、具体怎么改善，逐项讲清楚。",
              "field": {
                "measures": "测什么",
                "why": "为什么重要",
                "scoring": "评分逻辑"
              },
              "categories": {
                "crawlability": {
                  "title": "一、基础可抓取性",
                  "description": "决定 AI 爬虫能否到达并索引你页面的底层信号。这一层不过，后面的一切都白搭。",
                  "items": {
                    "https": {
                      "name": "HTTPS 安全协议",
                      "measures": "检测你的站点是否通过 HTTPS 提供服务、TLS 证书是否合法有效。",
                      "why": "AI 引擎爬虫（OpenAI GPTBot、Anthropic ClaudeBot、Perplexity、Google-Extended）会直接跳过非 HTTPS 站点。明文 HTTP 页面无论是在训练阶段还是实时检索阶段都会被降权或直接忽略。",
                      "scoring": "HTTPS 且证书有效 → PASS；HTTP 自动重定向到 HTTPS 可接受；纯 HTTP 或证书过期 → FAIL。"
                    },
                    "robots": {
                      "name": "robots.txt 爬虫规则",
                      "measures": "检测 robots.txt 是否存在、语法是否合法、是否明确放行主流 AI 爬虫（GPTBot、ClaudeBot、PerplexityBot、Google-Extended、CCBot）。",
                      "why": "AI 爬虫默认尊重 robots.txt。一条 Disallow: / 或 User-agent: GPTBot 的屏蔽就能让 OpenAI 永远看不到你的内容，相当于主动从 AI 视野里消失。",
                      "scoring": "文件存在且放行所有主流 AI 爬虫 → PASS；屏蔽 1–2 个次要爬虫 → WARN；屏蔽 GPTBot / ClaudeBot / CCBot 中任意一个 → FAIL。"
                    },
                    "sitemap": {
                      "name": "sitemap.xml 站点地图",
                      "measures": "检测 sitemap.xml 是否存在、格式合法、URL 数量合理，以及是否在 robots.txt 中被引用。",
                      "why": "AI 爬虫通过 sitemap 发现你所有页面，而不是从首页顺链接爬。缺失 sitemap 意味着爬虫可能只抓到首页就离开，深度内容对 LLM 完全隐形。",
                      "scoring": "文件存在且 URL 数量合理 → PASS；存在但 URL 很少或格式异常 → WARN；缺失 → FAIL。"
                    },
                    "llms": {
                      "name": "llms.txt LLM 索引文件",
                      "measures": "检测根目录下是否存在 llms.txt。这是 2024 年提出的新协议，用 Markdown 格式告诉大模型你站点是什么、核心内容在哪里。",
                      "why": "llms.txt 给 AI 爬虫一份「策划过的导览地图」——它不用猜哪些页面是精华，直接按你列的顺序抓。Anthropic、Perplexity 等已开始支持或参考这个协议，先做先占位。",
                      "scoring": "文件存在 → PASS；缺失 → INFO（目前非强制，但成本低、先做先赢）。"
                    },
                    "aiCrawlerAccess": {
                      "name": "AI 爬虫实测可访问性",
                      "measures": "用 GPTBot、ClaudeBot、PerplexityBot 等真实 User-Agent 发 HTTP 请求，检查你的 WAF、Cloudflare Bot Fight、验证码是否把它们挡在门外。",
                      "why": "robots.txt 放行只是第一层——Cloudflare Bot Fight 或 AWS WAF 常常把所有非浏览器 UA 一律当恶意爬虫拦掉，AI 爬虫也会被误伤。robots 策略和边缘策略必须一致。",
                      "scoring": "所有 AI UA 返回 200 → PASS；部分被拦 → WARN；全部被拦 → FAIL。"
                    }
                  }
                },
                "structuredData": {
                  "title": "二、结构化数据",
                  "description": "让大语言模型不用从散文里猜你是谁、你卖什么——这些是机器可读的信号。",
                  "items": {
                    "jsonld": {
                      "name": "JSON-LD 结构化数据",
                      "measures": "检测首页是否有 JSON-LD 代码块，以及是否描述了 Organization、Product、WebSite 等 schema.org 类型，核心字段是否填齐。",
                      "why": "结构化数据是你能给 LLM 的最机器可读的信号。ChatGPT、Perplexity、Google AI Overviews 都依赖 schema.org 标记来确认实体身份和抽取事实——缺了它，模型只能从散文里猜，猜错的代价就是被加免责声明或干脆忽略。",
                      "scoring": "Organization + Product / WebSite / FAQ 中任一，且核心字段齐全 → PASS；只有单个瘦身块 → WARN；完全没有 → FAIL。"
                    },
                    "metaTags": {
                      "name": "Meta 标签覆盖",
                      "measures": "检测首页的 title、meta description、canonical、viewport、Open Graph（og:title / og:description / og:image）、以及 Twitter Card 标签。",
                      "why": "Meta 标签是 AI 决定页面是否相关时看的那两行摘要。描述缺失意味着模型得自己生成一段——或者干脆跳过这个页面。og:image 也是 AI 聊天答案里链接预览卡片渲染的底图。",
                      "scoring": "6 条核心信号全都有 → PASS；4–5 条 → WARN；少于 4 条 → FAIL。"
                    },
                    "breadcrumbs": {
                      "name": "面包屑与知识面板标记",
                      "measures": "检查 BreadcrumbList JSON-LD、可见的面包屑导航，以及知识面板友好的标记（`sameAs`、`logo`、`SearchAction`）。",
                      "why": "面包屑帮 AI 理解你的站点结构——一个页面在品类树的什么位置。知识面板标记是 Google 和 Perplexity 构造查询旁那个「摘要框」的原料。两者都会随时间累积发挥作用。",
                      "scoring": "两类信号都有 → PASS；只有其中一类 → WARN；都没有 → FAIL。"
                    },
                    "answerFormat": {
                      "name": "AI 答案格式优化",
                      "measures": "检测内容是否以 LLM 容易直接引用的格式撰写——FAQ schema、问答模式、定义式开头段落、简洁直接的答案。",
                      "why": "LLM 偏好可以直接引用、自成一句的源。`「X 是一种 [品类]，它 [价值主张]」` 比把答案埋在营销段落里好得多。FAQ schema 明确标记了问答对，告诉 AI 这些是可抽取的答案。",
                      "scoring": "有 FAQ schema + 清晰的定义式开头 → PASS；部分具备 → WARN；完全没有可引用结构 → FAIL。"
                    }
                  }
                },
                "authority": {
                  "title": "三、权威信号",
                  "description": "LLM 用来判断你是否值得被推荐的站外证据——在训练语料里、百科里、评论平台里、媒体里的存在感。",
                  "items": {
                    "commonCrawl": {
                      "name": "AI 训练数据收录 (Common Crawl)",
                      "measures": "在 Common Crawl 最新一期全网快照里查你的域名，统计有多少页面被采入这个公开网页语料库。",
                      "why": "Common Crawl 是 ChatGPT、Claude、LLaMA 以及几乎所有开源 LLM 的训练数据。如果你没进 Common Crawl，这些模型在训练时从没见过你——用户问到品牌相关问题时，它们对你零记忆，结果要么加免责声明要么直接忽略。",
                      "scoring": "找到页面 → PASS；未收录但域名 < 60 天 → INFO（新站正常）；未收录且老站 → WARN；未收录 + robots.txt 屏蔽了 CCBot → FAIL。"
                    },
                    "wikipedia": {
                      "name": "Wikipedia / Wikidata 实体",
                      "measures": "检测你的品牌或产品是否在 Wikipedia（中英文）有词条，以及是否有 Wikidata Q-item。",
                      "why": "Wikipedia 和 Wikidata 是 LLM 用来验证实体的最权威结构化源。如果 ChatGPT 能在 Wikidata 上查到你，它就把你当真实实体对待；否则你会得到 `我对这个品牌不太确定` 的免责声明——这基本等于把推荐机会让出去。",
                      "scoring": "Wikipedia 词条 + Wikidata Q-item 都有 → PASS；只有其一 → WARN；都没有 → FAIL。"
                    },
                    "knowledgeGraph": {
                      "name": "Google 知识图谱收录",
                      "measures": "通过 schema.org 标记和 Google Knowledge Graph API 查询你的品牌是否在 Google 知识图谱里——就是那个出现在 Google 搜索右侧的实体侧栏。",
                      "why": "Google 的知识图谱直接喂给 Google AI Overviews、SGE 和 Gemini。在图谱里的实体会被引用，不在的就不会。同时它也为其他把 Google 结果作为参考的 LLM 播下了品牌事实种子。",
                      "scoring": "实体被找到且字段丰富 → PASS；部分覆盖 → WARN；未找到 → FAIL。"
                    },
                    "reviews": {
                      "name": "第三方评论与评分",
                      "measures": "检测你在所在品类的主流评论平台上是否有存在感——G2、Capterra、Trustpilot、Glassdoor、Yelp、TripAdvisor、CNET、Product Hunt 等。",
                      "why": "AI 引擎比较竞品时会给用户评论很高权重。G2 上有 100 条 4.5 星评论的品牌会被推荐，没有任何评论的会被跳过换成一个 `被真实用户验证过` 的品牌。LLM 在不同品类读的平台也不同。",
                      "scoring": "在 3+ 个品类相关平台有存在 → PASS；1–2 个 → WARN；都没有 → FAIL。"
                    },
                    "mentions": {
                      "name": "权威媒体与新闻提及",
                      "measures": "搜索高权重新闻和行业媒体中对你品牌的提及——WSJ、NYT、TechCrunch、Forbes、Bloomberg、行业分析师报告。",
                      "why": "权威性会复利累积。一篇 TechCrunch 文章的分量大于一百个随机博客的反链。训练在新闻数据集（GDELT、CCNews、RSS dumps）上的 LLM 把这些提及当作构建品牌实体画像的事实基准。",
                      "scoring": "3+ 条高权重提及 → PASS；1–2 条 → WARN；都没有 → FAIL。"
                    }
                  }
                },
                "visibility": {
                  "title": "四、AI 直接可见性",
                  "description": "滞后指标——衡量真实 AI 引擎在回答品类问题时是否真的提到并引用你。上面所有项都是先行指标，这一层才是真正决定生意的数字。",
                  "items": {
                    "citationRate": {
                      "name": "AI 引用率",
                      "measures": "通过 OpenRouter 把一组品牌相关和品类相关的问题发给 Perplexity，统计你的域名作为引用来源出现的频率。",
                      "why": "最终考试：用户在品类问题里问 AI，AI 会不会指向你？词典里其它所有项都是先行指标，引用率才是真正驱动收入的滞后指标。",
                      "scoring": "≥80% 引用率 → A（优秀）；60–79% → B；40–59% → C；20–39% → D；<20% → F。"
                    },
                    "answerInclusion": {
                      "name": "答案提及率",
                      "measures": "比引用率更柔和的指标——统计你的品牌名字是否在 AI 生成的答案里出现，无论是否带可点击的引用链接。",
                      "why": "没带引用链接的提及照样能赢心智。用户读到 `像 X、Y、Z 这样的品牌都…` 即使不点击也会记住名字。提及率是引用率的先行指标——先被提起，后被引用。",
                      "scoring": "≥60% 相关查询里被提及 → PASS；30–59% → WARN；<30% → FAIL。"
                    },
                    "shareOfVoice": {
                      "name": "竞品声量占比",
                      "measures": "在同一批品类查询里，统计哪些竞品品牌和你一起被提及、各自出现频率如何。",
                      "why": "你不只想被提及——你想在主要竞品**之前**被提及、**比他们更频繁**被提及。这个指标告诉你：AI 把你感知成品类领导者、挑战者、还是陪跑？",
                      "scoring": "你的品牌在被提及频率前 3 → PASS；在前 10 → WARN；没被提及 → FAIL。"
                    },
                    "sentimentFraming": {
                      "name": "品牌情感与框架",
                      "measures": "分析 AI 描述你品牌时的情感基调和叙事框架——是 `创新领导者`、`挑战者`、`小众玩家`、`曾出过问题`、还是 `有争议`？",
                      "why": "AI 在训练期捕捉到的品牌框架会固定好几年。训练数据里被框为 `创新者` 的品牌会被主动推荐；被框为 `曾出过问题` 的品牌即使情况早已改善，也会被加免责声明。",
                      "scoring": "≥60% 正面或中性框架 → PASS；30–59% → WARN；<30% 或频繁被加免责声明 → FAIL。"
                    },
                    "contentGaps": {
                      "name": "内容缺口",
                      "measures": "识别你品类里 AI 找不到好答案的那些问题——这些是竞品写了权威内容而你没有的地方。",
                      "why": "每一个未填的内容缺口都是竞品占掉的用户旅程。填补缺口是最直接的提升引用率方式——新页面被抓取后 AI 会立刻开始指向它。",
                      "scoring": "0 个重大缺口 → PASS；1–3 个 → WARN；4+ → FAIL。"
                    }
                  }
                },
                "entity": {
                  "title": "五、实体识别度",
                  "description": "大语言模型是否把你品牌当作一个真实、独立、能被自信描述的实体——这是 AI 可见性所有其它层面的地基。",
                  "items": {
                    "entityClarity": {
                      "name": "实体清晰度",
                      "measures": "让 AI 描述你的品牌，然后打分看描述是否准确、具体、完整。模型知道你做什么、服务谁、和别人有什么不同吗？",
                      "why": "如果 AI 无法简洁地描述你是什么，它就无法推荐你。`我觉得他们做一些和 AI 相关的东西` 属于失败状态——用户会重新提问，而竞品在 AI 犹豫的这几秒里就插队了。",
                      "scoring": "描述准确且具体 → PASS；模糊或部分错误 → WARN；困惑或完全不知道 → FAIL。"
                    },
                    "categoryAssociation": {
                      "name": "品类关联度",
                      "measures": "检测用户问品类问题时，AI 是否把你的品牌放进正确的心智格子。问 `最好的 X 工具` 时你会出现吗？问到错误品类时你是否缺席？",
                      "why": "大部分购买决策走的是品类意图。如果 AI 把你归到错误品类（或根本没品类归属），你对正在做调研的用户就是隐形的——他们永远看不到你的名字。",
                      "scoring": "≥70% 查询里被放进正确品类 → PASS；30–69% → WARN；<30% → FAIL。"
                    },
                    "platformCoverage": {
                      "name": "多平台覆盖",
                      "measures": "检测你在 LLM 训练最密集的平台上是否有经过验证的存在：Wikipedia、Wikidata、Crunchbase、LinkedIn、GitHub、Reddit、Product Hunt、Hacker News、行业目录。",
                      "why": "每多一个高权重平台都是 AI 用来构建你实体画像的一份交叉证据。覆盖 6+ 个平台的品牌会被自信地推荐；只有一个官网的会被当作未验证，推荐时会被加免责声明。",
                      "scoring": "覆盖 ≥6 / 10 个关键平台 → PASS；3–5 个 → WARN；<3 → FAIL。"
                    },
                    "recognitionRate": {
                      "name": "识别率",
                      "measures": "跨多个 AI 引擎和多种提问变体，统计模型能在不需要 URL 或额外消歧的情况下直接认出你品牌名的比例。",
                      "why": "识别是推荐的前提。如果 AI 每次都要反问 `你说的是哪个 X？`，用户就会流失到一个模型已经直接知道名字的品牌那里。",
                      "scoring": "≥80% 识别率 → PASS；50–79% → WARN；<50% → FAIL。"
                    },
                    "stability": {
                      "name": "答案稳定性",
                      "measures": "在同一个 AI 引擎上多次跑相同查询，检测答案是否一致。不稳定的答案说明训练期的 grounding 很薄弱。",
                      "why": "同一个问题一次给出 `X 是个好工具`、下一次给出 `没听说过 X`——用户会相信不确定的那次并流失。稳定即信任，信任才能换来推荐。",
                      "scoring": "≥90% 答案一致 → PASS；70–89% → WARN；<70% → FAIL。"
                    }
                  }
                }
              }
            }
          },
          "productsServices": {
            "title": "产品 & 服务",
            "description": "我们提供一系列GEO检测和优化服务，帮助您的网站在全球范围内获得最佳的搜索可见性。",
            "sections": {
              "ourServices": "我们的服务",
              "contactConsultation": "联系咨询"
            },
            "cta": {
              "tryNow": "立即试用",
              "subscribeNow": "立即订阅",
              "contactSales": "联系销售",
              "popular": "热门推荐"
            },
            "cards": {
              "free": {
                "name": "注册会员",
                "period": "/月",
                "description": "注册即可体验 GEO 基础检测，适合个人用户初步尝鲜",
                "features": [
                  "注册即可使用",
                  "5 项基础检测（17 个子检测点）",
                  "每月 3 次检测",
                  "即时结果展示"
                ]
              },
              "detector": {
                "name": "检测会员",
                "period": "/月",
                "description": "需要完整自助检测结果的网站管理员和个人开发者",
                "features": [
                  "需登录账号",
                  "23 项完整检测（全部子项）",
                  "每月 20 次检测",
                  "检测项优先级排序",
                  "完整 23 类报告（AI Visibility Score + 字母等级 + 可视化）",
                  "检测历史记录（可回看）",
                  "基础技术支持（工单 / 邮件）"
                ]
              },
              "starter": {
                "name": "基础版",
                "period": "/月",
                "description": "初阶团队的无限检测与优化建议",
                "features": [
                  "无限检测次数",
                  "23 项完整检测 + 详细优化建议",
                  "基础 GEO 覆盖",
                  "OpenAI / Gemini / Anthropic 收录规范",
                  "网站文案建设与优化",
                  "核心产品信息合规配置",
                  "按月持续交付"
                ]
              },
              "growth": {
                "name": "进阶版",
                "period": "/月",
                "description": "增长期品牌的无限检测 + 优化建议 + 榜单投放",
                "features": [
                  "无限检测次数",
                  "23 项完整检测 + 详细优化建议",
                  "海外主流 LLM 收录规范适配",
                  "含使用场景级文案与合规配置",
                  "付费榜单 SEO 低成本投放位",
                  "优先技术支持（< 24h 响应）",
                  "按月持续交付"
                ]
              },
              "scale": {
                "name": "旗舰版",
                "period": "",
                "description": "大型企业的全渠道 GEO 解决方案 + PR，联系销售定制",
                "getDemoPrice": "专属定制",
                "features": [
                  "无限检测次数 + 详细优化建议",
                  "全渠道定制化 GEO 覆盖",
                  "全渠道海外 LLM 收录规则",
                  "声誉管理：每月 3–5 篇榜单文章 + 反向链接",
                  "PR 支持（媒体报道对接）"
                ]
              }
            },
            "loadingMemberships": "加载套餐中…",
            "perProject": "起 / 项目",
            "selectPlaceholder": "请选择套餐",
            "submitting": "提交中…",
            "submitError": "提交失败，请稍后再试",
            "closeAria": "关闭",
            "table": {
              "headers": {
                "number": "#",
                "feature": "权益项",
                "free": "注册会员",
                "detector": "检测会员",
                "starter": "基础版",
                "growth": "进阶版",
                "scale": "旗舰版"
              },
              "rows": {
                "price": "价格",
                "type": "形态",
                "loginRequired": "是否需要注册登录",
                "checkItems": "检测大项数量",
                "subCheckItems": "子检测点数量",
                "monthlyChecks": "每月检测次数",
                "optimizationDetails": "优化建议（修复方案）详细度",
                "prioritySorting": "检测项优先级排序",
                "fullReport": "完整报告（23 类全量）",
                "history": "检测历史记录",
                "support": "技术支持",
                "basicGeo": "基础 GEO 覆盖",
                "llmStandards": "海外主流 LLM 收录规范",
                "websiteCopy": "网站文案建设与优化",
                "productInfo": "核心产品信息合规配置",
                "maintenance": "持续维护",
                "seoPlacement": "付费榜单 SEO 投放",
                "reputation": "声誉管理",
                "prSupport": "公关（PR）支持",
                "serviceCycle": "服务周期 / 输出节奏"
              },
              "values": {
                "free": {
                  "price": "$0/月",
                  "type": "自助 SaaS",
                  "loginRequired": "✅ 需登录",
                  "checkItems": "5 项",
                  "subCheckItems": "17 个",
                  "monthlyChecks": "3 次",
                  "optimizationDetails": "❌",
                  "prioritySorting": "❌",
                  "fullReport": "❌",
                  "history": "❌",
                  "support": "❌",
                  "basicGeo": "❌",
                  "llmStandards": "❌",
                  "websiteCopy": "❌",
                  "productInfo": "❌",
                  "maintenance": "❌",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "❌"
                },
                "detector": {
                  "price": "$9.99/月",
                  "type": "自助 SaaS",
                  "loginRequired": "✅ 需登录",
                  "checkItems": "23 项",
                  "subCheckItems": "全部子项",
                  "monthlyChecks": "20 次",
                  "optimizationDetails": "❌",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "✅ 基础",
                  "basicGeo": "❌",
                  "llmStandards": "❌",
                  "websiteCopy": "❌",
                  "productInfo": "❌",
                  "maintenance": "❌",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "❌"
                },
                "starter": {
                  "price": "$999/月",
                  "type": "SaaS 订阅",
                  "loginRequired": "✅ 需登录",
                  "checkItems": "23 项",
                  "subCheckItems": "全部子项",
                  "monthlyChecks": "无限",
                  "optimizationDetails": "✅ 详细优化建议",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "基础工单",
                  "basicGeo": "✅ 基础",
                  "llmStandards": "✅ 完成",
                  "websiteCopy": "✅ 全面",
                  "productInfo": "✅",
                  "maintenance": "✅ 持续",
                  "seoPlacement": "❌",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "按月订阅"
                },
                "growth": {
                  "price": "$2,500/月",
                  "type": "SaaS 订阅",
                  "loginRequired": "✅ 需登录",
                  "checkItems": "23 项",
                  "subCheckItems": "全部子项",
                  "monthlyChecks": "无限",
                  "optimizationDetails": "✅ 详细优化建议",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "优先响应 < 24h",
                  "basicGeo": "✅ 基础",
                  "llmStandards": "✅ 适配",
                  "websiteCopy": "✅ 含使用场景",
                  "productInfo": "✅ 使用场景级",
                  "maintenance": "✅ 持续",
                  "seoPlacement": "✅ 低成本投放位",
                  "reputation": "❌",
                  "prSupport": "❌",
                  "serviceCycle": "按月订阅"
                },
                "scale": {
                  "price": "专属定制",
                  "type": "定制企业服务",
                  "loginRequired": "✅ 需登录",
                  "checkItems": "23 项",
                  "subCheckItems": "全部子项",
                  "monthlyChecks": "无限",
                  "optimizationDetails": "✅ 详细优化建议",
                  "prioritySorting": "✅",
                  "fullReport": "✅",
                  "history": "✅",
                  "support": "24/7 + 专属顾问",
                  "basicGeo": "✅ 全渠道定制化",
                  "llmStandards": "✅ 全渠道",
                  "websiteCopy": "✅ 定制化交易场景",
                  "productInfo": "✅ 定制化 + 校验",
                  "maintenance": "✅ 长期",
                  "seoPlacement": "✅ 性价比监测",
                  "reputation": "✅ 3–5 篇/月 + 反向链接",
                  "prSupport": "✅ 媒体报道",
                  "serviceCycle": "月度持续输出"
                }
              }
            },
            "services": [
              {
                "title": "基础版",
                "description": "基础 GEO 覆盖: $2,000–$3,000\n付费榜单投放: 完成海外主流大模型（OpenAI/Gemini/Anthropic）收录规范，网站文案的建设和优化，核心产品信息合规配置及持续维护\n最佳榜单 SEO: 无（不包含）\n声誉管理: 无（不包含）\n公关（PR）: 无（不包含）",
                "price": "$2,000–$3,000",
                "features": [
                  "基础 GEO 覆盖",
                  "完成海外主流大模型收录规范",
                  "网站文案建设和优化",
                  "核心产品信息合规配置及持续维护"
                ],
                "button": "了解详情"
              },
              {
                "title": "进阶版",
                "description": "基础 GEO 覆盖: $4,000–$7,000\n付费榜单投放: 适配海外主流大模型（OpenAI/Gemini/Anthropic）收录规范，网站文案的建设和优化，核心产品使用场景信息合规配置及持续维护\n最佳榜单 SEO: 在排名类网站采购低成本投放位\n声誉管理: 创建并发布1–2篇顶级榜单文章（例如“2026最佳XX平台”），在活动开始时发布，并针对大型语言模型的检索和引用进行优化\n公关（PR）: 无（不包含）",
                "price": "$4,000–$7,000",
                "features": [
                  "基础 GEO 覆盖",
                  "适配海外主流大模型收录规范",
                  "在排名类网站采购低成本投放位",
                  "创建并发布1–2篇顶级榜单文章"
                ],
                "button": "了解详情"
              },
              {
                "title": "Scale",
                "description": "基础 GEO 覆盖: $8,000–$12,000\n付费榜单投放: 全渠道海外大模型 GEO 收录规则，定制化完成交易产品核心使用场景、信息校验及持续维护\n最佳榜单 SEO: 监测排名类网站的投放成本，确定性价比最高的投放预算\n声誉管理: 每月撰写并发布 3-5 篇最佳榜单类文章，配套数据类文章、反向链接和验证页面支持\n公关（PR）: 可配合企业内部团队或专属公关机构，助力获得媒体报道",
                "price": "$8,000–$12,000",
                "features": [
                  "全渠道海外大模型 GEO 收录规则",
                  "监测排名类网站的投放成本",
                  "每月撰写并发布 3-5 篇最佳榜单类文章",
                  "可配合企业内部团队或专属公关机构"
                ],
                "button": "了解详情"
              }
            ],
            "contact": {
              "getCustomPlan": "获取定制方案",
              "name": "姓名",
              "namePlaceholder": "您的姓名",
              "email": "邮箱",
              "emailPlaceholder": "您的邮箱",
              "website": "网站",
              "websitePlaceholder": "moltspay.com",
              "service": "感兴趣的服务",
              "serviceOptions": [
                "基础检测服务",
                "高级检测服务",
                "GEO优化定制服务"
              ],
              "message": "留言",
              "messagePlaceholder": "告诉我们您的需求",
              "submit": "提交咨询",
              "contactUs": "联系我们",
              "contactText": "如有任何疑问或需要更多信息，请通过以下方式联系我们。我们的专业团队将尽快回复您。",
              "emailLabel": "邮箱",
              "phoneLabel": "电话"
            }
          },
          "account": {
            "layout": {
              "subtitle": "账户信息",
              "logout": "退出登录"
            },
            "menu": {
              "profile": "个人资料",
              "membership": "会员信息",
              "usage": "使用情况",
              "history": "检测记录"
            },
            "common": {
              "needLogin": "请先登录"
            },
            "profile": {
              "accountInfo": "账号信息",
              "email": "邮箱",
              "status": "账号状态",
              "active": "正常",
              "inactive": "已停用",
              "userId": "用户 ID",
              "changePassword": "修改密码",
              "oldPassword": "当前密码",
              "newPassword": "新密码",
              "confirmPassword": "确认新密码",
              "submit": "更新密码",
              "submitting": "提交中...",
              "success": "密码修改成功",
              "failed": "修改失败",
              "minLength": "新密码至少 6 位",
              "mismatch": "两次输入的新密码不一致"
            },
            "membership": {
              "currentPlan": "当前套餐",
              "service": "人工服务",
              "contactSales": "联系销售定制",
              "startDate": "开始时间",
              "endDate": "到期时间",
              "permanent": "长期有效",
              "daysLeft": "剩余天数",
              "days": "天",
              "upgrade": "升级套餐",
              "browse": "浏览套餐",
              "renew": "续费",
              "cancel": "取消会员",
              "cancelConfirm": "确定要取消会员吗？",
              "renewHint": "支付功能即将上线，请稍后再试或联系客服。",
              "features": "套餐权益"
            },
            "usage": {
              "monthlyUsage": "本月检测用量",
              "times": "次",
              "unlimited": "您当前套餐无检测次数限制。",
              "remaining": "剩余 {{n}} 次，月初自动重置。",
              "unlockedCategories": "已解锁检测类目",
              "allUnlocked": "您已解锁全部 23 项检测能力。",
              "partialUnlocked": "升级套餐可解锁更多检测类目。"
            },
            "history": {
              "title": "检测记录",
              "total": "共 {{n}} 条",
              "empty": "还没有检测记录",
              "goCheck": "去检测一下",
              "time": "时间",
              "url": "网址",
              "score": "得分",
              "mode": "模式",
              "modeFree": "标准",
              "modeAdvanced": "高级",
              "actions": "操作",
              "view": "查看",
              "loading": "加载中...",
              "delete": "删除",
              "deleteConfirm": "确定删除这条检测记录吗？",
              "prev": "上一页",
              "next": "下一页",
              "pageOf": "第 {{page}} / {{pages}} 页"
            }
          }
        }
      }
    },
    lng: localStorage.getItem('i18nextLng') || 'zh',
    fallbackLng: 'en',
    defaultNS: 'translation',
    interpolation: {
      escapeValue: false
    }
  });
}

export default i18n;
