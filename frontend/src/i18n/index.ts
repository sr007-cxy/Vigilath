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
            "signedInAs": "Signed in as"
          },
          "common": {
            "error": "Error",
            "loading": "Loading...",
            "contact": "Login",
            "cancel": "Logout",
            "success": "Success"
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
              "invalid": "Please enter a valid URL"
            },
            "poweredBy": "Powered by GEO AI",
            "contactLink": "Need expert optimization help?",
            "advanced": {
              "badge": "Members Only",
              "title": "Advanced Detection",
              "subtitle": "Unlock professional GEO tools to measure how AI engines see your brand across the web.",
              "upgrade": "Upgrade to unlock",
              "comingSoon": "Coming soon",
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
              "modeDefault": "Standard check (5 / 23 categories)",
              "modeLockedHint": "Upgrade your plan to unlock this mode"
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
              "share": "Share"
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
            "description": "Learn about the core concepts and best practices of GEO strategy to optimize your website for AI-powered search engines.",
            "sections": {
              "about": "About GEO",
              "whatIsGeo": "What is GEO?",
              "whyGeoImportant": "Why is GEO important?",
              "whyGeoPoints": [
                "Improve search visibility for global users",
                "Adapt to different regional search algorithm preferences",
                "Enhance user experience and conversion rates",
                "Strengthen brand's global influence"
              ],
              "strategies": "GEO Strategies",
              "contentLocalization": "Content Localization",
              "contentLocalizationDesc": "Provide localized content for users in different regions, including language, cultural references, and region-specific information.",
              "contentLocalizationPoints": [
                "Translate and localize content",
                "Use region-specific keywords",
                "Adapt to regional cultural differences"
              ],
              "technicalOptimization": "Technical Optimization",
              "technicalOptimizationDesc": "Ensure your website technically supports GEO optimization, including site structure, loading speed, and mobile-friendliness.",
              "technicalOptimizationPoints": [
                "Use hreflang tags",
                "Optimize website loading speed",
                "Ensure mobile-friendliness"
              ],
              "keyData": "GEO Key Data",
              "importantMetrics": "Important GEO Metrics",
              "regionalTraffic": "Regional Traffic Distribution",
              "regionalTrafficDesc": "Understand user access patterns from different regions to optimize targeted content.",
              "languagePreference": "Language Preference",
              "languagePreferenceDesc": "Analyze user language preferences to provide corresponding localized content.",
              "searchTrends": "Search Trends",
              "searchTrendsDesc": "Track search trends in different regions to adjust keyword strategies."
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
                "getDemoPrice": "Get a Demo",
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
                  "basicGeo": "—",
                  "llmStandards": "—",
                  "websiteCopy": "—",
                  "productInfo": "—",
                  "maintenance": "—",
                  "seoPlacement": "—",
                  "reputation": "—",
                  "prSupport": "—",
                  "serviceCycle": "—"
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
                  "basicGeo": "—",
                  "llmStandards": "—",
                  "websiteCopy": "—",
                  "productInfo": "—",
                  "maintenance": "—",
                  "seoPlacement": "—",
                  "reputation": "—",
                  "prSupport": "—",
                  "serviceCycle": "—"
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
                  "price": "Get a Demo",
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
          }
        }
      },
      ja: {
        translation: {
          "result": {
            "shareExport": {
              "exportPDFLoading": "PDFをエクスポート中..."
            }
          },
          "nav": {
            "home": "ホーム",
            "geoKnowledge": "GEO 知識",
            "productsServices": "製品とサービス",
            "aboutUs": "会社概要",
            "about": "GEO について",
            "process": "プロセス",
            "pricing": "価格設定",
            "data": "インサイト",
            "contact": "お問い合わせ",
            "langSwitch": "中文",
            "login": "ログイン",
            "signedInAs": "ログイン中:"
          },
          "common": {
            "error": "エラー",
            "loading": "読み込み中...",
            "contact": "ログイン"
          },
          "hero": {
            "title": "AI にあなたのブランドを推奨させよう",
            "subtitle": "生成エンジン最適化 (GEO)",
            "description": "潜在顧客が ChatGPT、Gemini、Perplexity などの AI アシスタントに製品やサービスの推奨を求めたときに、あなたの企業が最初に推奨されるようにしましょう。",
            "cta": "詳しく知る",
            "ctaSecondary": "お問い合わせ"
          },
          "login": {
            "title": "ログイン",
            "subtitle": "または",
            "createAccount": "新規アカウントを作成",
            "email": "メールアドレス",
            "emailPlaceholder": "あなたのメールアドレス",
            "password": "パスワード",
            "passwordPlaceholder": "あなたのパスワード",
            "rememberMe": "ログイン状態を保持",
            "forgotPassword": "パスワードを忘れましたか？",
            "button": "ログイン",
            "loading": "ログイン中...",
            "error": "メールアドレスまたはパスワードが正しくありません"
          },
          "register": {
            "title": "アカウント作成",
            "subtitle": "または",
            "loginAccount": "既存のアカウントにログイン",
            "name": "名前",
            "namePlaceholder": "あなたの名前",
            "email": "メールアドレス",
            "emailPlaceholder": "あなたのメールアドレス",
            "password": "パスワード",
            "passwordPlaceholder": "あなたのパスワード",
            "confirmPassword": "パスワードの確認",
            "confirmPasswordPlaceholder": "パスワードを確認してください",
            "terms": "利用規約とプライバシーポリシーに同意します",
            "button": "登録",
            "loading": "登録中...",
            "error": "パスワードが一致しません"
          },
          "forgotPassword": {
            "title": "パスワードを忘れた",
            "description": "パスワードリセットリンクを受け取るためにメールアドレスを入力してください",
            "button": "リセットリンクを送信",
            "loading": "送信中...",
            "success": {
              "emailSent": "パスワードリセットメールを送信しました。指示に従うには受信トレイを確認してください。",
              "token": "リセットトークン",
              "resetSuccess": "パスワードのリセットに成功しました。新しいパスワードでログインできます。"
            },
            "error": {
              "sendFailed": "パスワードリセットメールの送信に失敗しました",
              "resetFailed": "パスワードのリセットに失敗しました"
            },
            "backToLogin": "ログインに戻る",
            "reset": {
              "title": "パスワードをリセット",
              "description": "以下に新しいパスワードを入力してください",
              "newPassword": "新しいパスワード",
              "newPasswordPlaceholder": "新しいパスワードを入力してください",
              "confirmPassword": "パスワードの確認",
              "confirmPasswordPlaceholder": "パスワードを確認してください",
              "button": "パスワードをリセット",
              "loading": "リセット中..."
            }
          }
        }
      },
      ko: {
        translation: {
          "result": {
            "shareExport": {
              "exportPDFLoading": "PDF 내보내는 중..."
            }
          },
          "nav": {
            "home": "홈",
            "geoKnowledge": "GEO 지식",
            "productsServices": "제품 및 서비스",
            "aboutUs": "회사 소개",
            "about": "GEO 소개",
            "process": "프로세스",
            "pricing": "가격 설정",
            "data": "인사이트",
            "contact": "연락처",
            "langSwitch": "中文",
            "login": "로그인",
            "signedInAs": "로그인 중:"
          },
          "common": {
            "error": "오류",
            "loading": "로딩 중...",
            "contact": "로그인"
          },
          "hero": {
            "title": "AI가 귀하의 브랜드를 추천하도록 하세요",
            "subtitle": "생성형 엔진 최적화 (GEO)",
            "description": "잠재 고객이 ChatGPT, Gemini, Perplexity 등 AI 어시스턴트에게 제품이나 서비스 추천을 요청할 때 귀하의 비즈니스가 처음으로 추천되도록 보장하세요.",
            "cta": "자세히 알아보기",
            "ctaSecondary": "연락하기"
          },
          "login": {
            "title": "로그인",
            "subtitle": "또는",
            "createAccount": "새 계정 만들기",
            "email": "이메일",
            "emailPlaceholder": "귀하의 이메일",
            "password": "비밀번호",
            "passwordPlaceholder": "귀하의 비밀번호",
            "rememberMe": "로그인 상태 유지",
            "forgotPassword": "비밀번호를 잊으셨나요?",
            "button": "로그인",
            "loading": "로그인 중...",
            "error": "이메일 또는 비밀번호가 잘못되었습니다"
          },
          "register": {
            "title": "계정 생성",
            "subtitle": "또는",
            "loginAccount": "기존 계정에 로그인",
            "name": "이름",
            "namePlaceholder": "귀하의 이름",
            "email": "이메일",
            "emailPlaceholder": "귀하의 이메일",
            "password": "비밀번호",
            "passwordPlaceholder": "귀하의 비밀번호",
            "confirmPassword": "비밀번호 확인",
            "confirmPasswordPlaceholder": "비밀번호를 확인하세요",
            "terms": "서비스 약관 및 개인 정보 보호 정책에 동의합니다",
            "button": "등록",
            "loading": "등록 중...",
            "error": "비밀번호가 일치하지 않습니다"
          },
          "forgotPassword": {
            "title": "비밀번호를 잊으셨나요",
            "description": "비밀번호 재설정 링크를 받기 위해 이메일을 입력하세요",
            "button": "재설정 링크 보내기",
            "loading": "전송 중...",
            "success": {
              "emailSent": "비밀번호 재설정 이메일을 보냈습니다. 지침을 따르려면 받은 편지를 확인하세요.",
              "token": "재설정 토큰",
              "resetSuccess": "비밀번호 재설정에 성공했습니다. 이제 새 비밀번호로 로그인할 수 있습니다."
            },
            "error": {
              "sendFailed": "비밀번호 재설정 이메일 전송에 실패했습니다",
              "resetFailed": "비밀번호 재설정에 실패했습니다"
            },
            "backToLogin": "로그인으로 돌아가기",
            "reset": {
              "title": "비밀번호 재설정",
              "description": "아래에 새 비밀번호를 입력하세요",
              "newPassword": "새 비밀번호",
              "newPasswordPlaceholder": "새 비밀번호를 입력하세요",
              "confirmPassword": "비밀번호 확인",
              "confirmPasswordPlaceholder": "비밀번호를 확인하세요",
              "button": "비밀번호 재설정",
              "loading": "재설정 중..."
            }
          }
        }
      },
      de: {
        translation: {
          "result": {
            "shareExport": {
              "exportPDFLoading": "PDF wird exportiert..."
            }
          },
          "nav": {
            "home": "Startseite",
            "geoKnowledge": "GEO Wissen",
            "productsServices": "Produkte & Dienstleistungen",
            "aboutUs": "Über uns",
            "about": "Über GEO",
            "process": "Prozess",
            "pricing": "Preise",
            "data": "Einblicke",
            "contact": "Kontakt",
            "langSwitch": "中文",
            "login": "Anmelden",
            "signedInAs": "Angemeldet als"
          },
          "common": {
            "error": "Fehler",
            "loading": "Laden...",
            "contact": "Anmelden"
          },
          "hero": {
            "title": "Lassen Sie AI Ihre Marke empfehlen",
            "subtitle": "Generative Engine Optimization (GEO)",
            "description": "Stellen Sie sicher, dass Ihr Unternehmen als erstes empfohlen wird, wenn potenzielle Kunden ChatGPT, Gemini, Perplexity und andere KI-Assistenten nach Produkt- oder Dienstleistungsempfehlungen fragen.",
            "cta": "Mehr erfahren",
            "ctaSecondary": "Kontaktieren Sie uns"
          },
          "login": {
            "title": "Anmelden",
            "subtitle": "oder",
            "createAccount": "Neues Konto erstellen",
            "email": "E-Mail",
            "emailPlaceholder": "Ihre E-Mail",
            "password": "Passwort",
            "passwordPlaceholder": "Ihr Passwort",
            "rememberMe": "Angemeldet bleiben",
            "forgotPassword": "Passwort vergessen?",
            "button": "Anmelden",
            "loading": "Anmelden...",
            "error": "Ungültige E-Mail oder Passwort"
          },
          "register": {
            "title": "Konto erstellen",
            "subtitle": "oder",
            "loginAccount": "Bei vorhandenen Konto anmelden",
            "name": "Name",
            "namePlaceholder": "Ihr Name",
            "email": "E-Mail",
            "emailPlaceholder": "Ihre E-Mail",
            "password": "Passwort",
            "passwordPlaceholder": "Ihr Passwort",
            "confirmPassword": "Passwort bestätigen",
            "confirmPasswordPlaceholder": "Bestätigen Sie Ihr Passwort",
            "terms": "Ich stimme den Nutzungsbedingungen und der Datenschutzrichtlinie zu",
            "button": "Registrieren",
            "loading": "Registrieren...",
            "error": "Passwörter stimmen nicht überein"
          },
          "forgotPassword": {
            "title": "Passwort vergessen",
            "description": "Geben Sie Ihre E-Mail ein, um einen Passwort-Zurücksetzungslink zu erhalten",
            "button": "Zurücksetzungslink senden",
            "loading": "Senden...",
            "success": {
              "emailSent": "Passwort-Zurücksetzungs-E-Mail gesendet. Überprüfen Sie Ihren Posteingang für Anweisungen.",
              "token": "Zurücksetzungs-Token",
              "resetSuccess": "Passwort erfolgreich zurückgesetzt. Sie können sich jetzt mit Ihrem neuen Passwort anmelden."
            },
            "error": {
              "sendFailed": "Fehler beim Senden der Passwort-Zurücksetzungs-E-Mail",
              "resetFailed": "Fehler beim Zurücksetzen des Passworts"
            },
            "backToLogin": "Zurück zur Anmeldung",
            "reset": {
              "title": "Passwort zurücksetzen",
              "description": "Geben Sie unten Ihr neues Passwort ein",
              "newPassword": "Neues Passwort",
              "newPasswordPlaceholder": "Geben Sie Ihr neues Passwort ein",
              "confirmPassword": "Passwort bestätigen",
              "confirmPasswordPlaceholder": "Bestätigen Sie Ihr Passwort",
              "button": "Passwort zurücksetzen",
              "loading": "Zurücksetzen..."
            }
          }
        }
      },
      fr: {
        translation: {
          "result": {
            "shareExport": {
              "exportPDFLoading": "Export PDF en cours..."
            }
          },
          "nav": {
            "home": "Accueil",
            "geoKnowledge": "Connaissances GEO",
            "productsServices": "Produits & Services",
            "aboutUs": "À propos de nous",
            "about": "À propos de GEO",
            "process": "Processus",
            "pricing": "Tarification",
            "data": "Insights",
            "contact": "Contact",
            "langSwitch": "中文",
            "login": "Connexion",
            "signedInAs": "Connecté en tant que"
          },
          "common": {
            "error": "Erreur",
            "loading": "Chargement...",
            "contact": "Connexion"
          },
          "hero": {
            "title": "Faites recommander votre marque par l'IA",
            "subtitle": "Optimisation de Moteur Génératif (GEO)",
            "description": "Assurez-vous que votre entreprise soit recommandée en premier lorsque des clients potentiels demandent des recommandations de produits ou services à ChatGPT, Gemini, Perplexity et autres assistants IA.",
            "cta": "En savoir plus",
            "ctaSecondary": "Contactez-nous"
          },
          "login": {
            "title": "Connexion",
            "subtitle": "ou",
            "createAccount": "Créer un nouveau compte",
            "email": "Email",
            "emailPlaceholder": "Votre email",
            "password": "Mot de passe",
            "passwordPlaceholder": "Votre mot de passe",
            "rememberMe": "Se souvenir de moi",
            "forgotPassword": "Mot de passe oublié ?",
            "button": "Connexion",
            "loading": "Connexion...",
            "error": "Email ou mot de passe invalide"
          },
          "register": {
            "title": "Créer un compte",
            "subtitle": "ou",
            "loginAccount": "Se connecter à un compte existant",
            "name": "Nom",
            "namePlaceholder": "Votre nom",
            "email": "Email",
            "emailPlaceholder": "Votre email",
            "password": "Mot de passe",
            "passwordPlaceholder": "Votre mot de passe",
            "confirmPassword": "Confirmer le mot de passe",
            "confirmPasswordPlaceholder": "Confirmez votre mot de passe",
            "terms": "J'accepte les conditions d'utilisation et la politique de confidentialité",
            "button": "S'inscrire",
            "loading": "Inscription...",
            "error": "Les mots de passe ne correspondent pas"
          },
          "forgotPassword": {
            "title": "Mot de passe oublié",
            "description": "Entrez votre email pour recevoir un lien de réinitialisation de mot de passe",
            "button": "Envoyer le lien de réinitialisation",
            "loading": "Envoi...",
            "success": {
              "emailSent": "Email de réinitialisation de mot de passe envoyé. Vérifiez votre boîte de réception pour les instructions.",
              "token": "Jeton de réinitialisation",
              "resetSuccess": "Mot de passe réinitialisé avec succès. Vous pouvez maintenant vous connecter avec votre nouveau mot de passe."
            },
            "error": {
              "sendFailed": "Échec de l'envoi de l'email de réinitialisation de mot de passe",
              "resetFailed": "Échec de la réinitialisation du mot de passe"
            },
            "backToLogin": "Retour à la connexion",
            "reset": {
              "title": "Réinitialiser le mot de passe",
              "description": "Entrez votre nouveau mot de passe ci-dessous",
              "newPassword": "Nouveau mot de passe",
              "newPasswordPlaceholder": "Entrez votre nouveau mot de passe",
              "confirmPassword": "Confirmer le mot de passe",
              "confirmPasswordPlaceholder": "Confirmez votre mot de passe",
              "button": "Réinitialiser le mot de passe",
              "loading": "Réinitialisation..."
            }
          }
        }
      },
      es: {
        translation: {
          "result": {
            "shareExport": {
              "exportPDFLoading": "Exportando PDF..."
            }
          },
          "nav": {
            "home": "Inicio",
            "geoKnowledge": "Conocimientos GEO",
            "productsServices": "Productos & Servicios",
            "aboutUs": "Sobre nosotros",
            "about": "Sobre GEO",
            "process": "Proceso",
            "pricing": "Precios",
            "data": "Informes",
            "contact": "Contacto",
            "langSwitch": "中文",
            "login": "Iniciar sesión",
            "signedInAs": "Sesión iniciada como"
          },
          "common": {
            "error": "Error",
            "loading": "Cargando...",
            "contact": "Iniciar sesión"
          },
          "hero": {
            "title": "Haz que la IA recomiende tu marca",
            "subtitle": "Optimización de Motor Generativo (GEO)",
            "description": "Asegúrate de que tu negocio sea recomendado primero cuando los clientes potenciales soliciten recomendaciones de productos o servicios a ChatGPT, Gemini, Perplexity y otros asistentes de IA.",
            "cta": "Más información",
            "ctaSecondary": "Contáctanos"
          },
          "login": {
            "title": "Iniciar sesión",
            "subtitle": "o",
            "createAccount": "Crear una nueva cuenta",
            "email": "Correo electrónico",
            "emailPlaceholder": "Tu correo electrónico",
            "password": "Contraseña",
            "passwordPlaceholder": "Tu contraseña",
            "rememberMe": "Recordarme",
            "forgotPassword": "¿Olvidaste tu contraseña?",
            "button": "Iniciar sesión",
            "loading": "Iniciando sesión...",
            "error": "Correo electrónico o contraseña inválidos"
          },
          "register": {
            "title": "Crear cuenta",
            "subtitle": "o",
            "loginAccount": "Iniciar sesión en una cuenta existente",
            "name": "Nombre",
            "namePlaceholder": "Tu nombre",
            "email": "Correo electrónico",
            "emailPlaceholder": "Tu correo electrónico",
            "password": "Contraseña",
            "passwordPlaceholder": "Tu contraseña",
            "confirmPassword": "Confirmar contraseña",
            "confirmPasswordPlaceholder": "Confirma tu contraseña",
            "terms": "Acepto los términos de servicio y la política de privacidad",
            "button": "Registrarse",
            "loading": "Registrando...",
            "error": "Las contraseñas no coinciden"
          },
          "forgotPassword": {
            "title": "Olvidé mi contraseña",
            "description": "Ingresa tu correo electrónico para recibir un enlace de restablecimiento de contraseña",
            "button": "Enviar enlace de restablecimiento",
            "loading": "Enviando...",
            "success": {
              "emailSent": "Correo electrónico de restablecimiento de contraseña enviado. Verifica tu bandeja de entrada para instrucciones.",
              "token": "Token de restablecimiento",
              "resetSuccess": "Contraseña restablecida con éxito. Ahora puedes iniciar sesión con tu nueva contraseña."
            },
            "error": {
              "sendFailed": "Error al enviar el correo electrónico de restablecimiento de contraseña",
              "resetFailed": "Error al restablecer la contraseña"
            },
            "backToLogin": "Volver al inicio de sesión",
            "reset": {
              "title": "Restablecer contraseña",
              "description": "Ingresa tu nueva contraseña a continuación",
              "newPassword": "Nueva contraseña",
              "newPasswordPlaceholder": "Ingresa tu nueva contraseña",
              "confirmPassword": "Confirmar contraseña",
              "confirmPasswordPlaceholder": "Confirma tu contraseña",
              "button": "Restablecer contraseña",
              "loading": "Restableciendo..."
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
            "signedInAs": "已登录为"
          },
          "common": {
            "error": "错误",
            "loading": "加载中...",
            "contact": "登录",
            "cancel": "退出",
            "success": "成功"
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
              "invalid": "请输入有效的 URL"
            },
            "poweredBy": "由 GEO AI 提供支持",
            "contactLink": "需要专家优化帮助？",
            "advanced": {
              "badge": "会员专享",
              "title": "高级检测能力",
              "subtitle": "开通会员，解锁专业 GEO 检测工具，全面洞察 AI 引擎对你的网站的认知。",
              "upgrade": "升级解锁",
              "comingSoon": "功能即将上线",
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
              "checkAnother": "检查另一个网站",
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
              "modeDefault": "标准检测（5 / 23 类）",
              "modeLockedHint": "升级会员解锁此模式"
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
              "share": "分享"
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
            "description": "了解 GEO 策略的核心概念和最佳实践，优化您的网站以适应 AI 驱动的搜索引擎。",
            "sections": {
              "about": "关于 GEO",
              "whatIsGeo": "什么是 GEO？",
              "whyGeoImportant": "为什么 GEO 很重要？",
              "whyGeoPoints": [
                "提高全球用户的搜索可见性",
                "适应不同地区的搜索算法偏好",
                "提升用户体验和转化率",
                "增强品牌的全球影响力"
              ],
              "strategies": "GEO 策略",
              "contentLocalization": "内容本地化",
              "contentLocalizationDesc": "为不同地区的用户提供本地化的内容，包括语言、文化参考和地区特定信息。",
              "contentLocalizationPoints": [
                "翻译和本地化内容",
                "使用地区特定的关键词",
                "适应地区文化差异"
              ],
              "technicalOptimization": "技术优化",
              "technicalOptimizationDesc": "确保网站在技术层面支持 GEO 优化，包括网站结构、加载速度和移动友好性。",
              "technicalOptimizationPoints": [
                "使用hreflang标签",
                "优化网站加载速度",
                "确保移动友好性"
              ],
              "keyData": "GEO 关键数据",
              "importantMetrics": "重要的 GEO 指标",
              "regionalTraffic": "地区流量分布",
              "regionalTrafficDesc": "了解不同地区的用户访问情况，优化针对性内容。",
              "languagePreference": "语言偏好",
              "languagePreferenceDesc": "分析用户语言偏好，提供相应的本地化内容。",
              "searchTrends": "搜索趋势",
              "searchTrendsDesc": "跟踪不同地区的搜索趋势，调整关键词策略。"
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
                "name": "Starter",
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
                "name": "Growth",
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
                "name": "Scale",
                "period": "",
                "description": "大型企业的全渠道 GEO 解决方案 + PR，联系销售定制",
                "getDemoPrice": "Get a Demo",
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
                "starter": "Starter",
                "growth": "Growth",
                "scale": "Scale"
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
                  "basicGeo": "—",
                  "llmStandards": "—",
                  "websiteCopy": "—",
                  "productInfo": "—",
                  "maintenance": "—",
                  "seoPlacement": "—",
                  "reputation": "—",
                  "prSupport": "—",
                  "serviceCycle": "—"
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
                  "basicGeo": "—",
                  "llmStandards": "—",
                  "websiteCopy": "—",
                  "productInfo": "—",
                  "maintenance": "—",
                  "seoPlacement": "—",
                  "reputation": "—",
                  "prSupport": "—",
                  "serviceCycle": "—"
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
                  "price": "Get a Demo",
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
                "title": "Starter",
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
                "title": "Growth",
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
