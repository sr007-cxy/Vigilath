/**
 * Main site footer: four columns (site nav / contact & complaints / filings & licenses / company info) + a bottom copyright row; two / one column on narrow screens.
 * Layout matches the white-label footer (white-label/shell/Footer.tsx); the two are maintained separately -- this one carries our brand and must not be used by white-label.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ICP_BEIAN, POLICE_BEIAN, EDI_LICENSE } from '../config/region';

const COMPANY = '<company-name>';
const COMPANY_ADDRESS = '<company-address>';
const CONTACT_EMAIL = '<support-email>';

const linkClass = 'text-secondary hover:text-accent-primary hover:underline transition-colors';
const headingClass = 'text-sm font-semibold text-primary mb-3';

export function Footer() {
  const { t } = useTranslation();
  // Same as the header nav (except home)
  const siteLinks = [
    { to: '/about', label: t('nav.aboutUs') },
    { to: '/geo-knowledge', label: t('nav.geoKnowledge') },
    { to: '/products-services', label: t('nav.productsServices') },
    { to: '/cases', label: t('nav.cases') },
    { to: '/blog', label: t('nav.blog') },
    { to: '/faq', label: t('nav.faq') },
  ];
  const legalLinks = [
    { to: '/terms', label: t('footer.links.terms') },
    { to: '/privacy', label: t('footer.links.privacy') },
    { to: '/cookie-policy', label: t('footer.links.cookie') },
  ];
  return (
    <footer className="mt-auto" style={{ background: 'var(--bg-card)' }}>
      <div className="max-w-6xl mx-auto px-6 pt-10 pb-6 text-sm text-secondary leading-relaxed">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-[auto_auto_auto_minmax(0,1fr)] gap-x-10 gap-y-8">
          <div>
            <p className={headingClass}>Vigilath</p>
            <ul className="space-y-2">
              {siteLinks.map((l) => (
                <li key={l.to}><Link to={l.to} className={linkClass}>{l.label}</Link></li>
              ))}
              <li>
                {legalLinks.map((l, i) => (
                  <span key={l.to}>
                    {i > 0 && <span className="mx-1.5 text-muted">|</span>}
                    <Link to={l.to} className={linkClass}>{l.label}</Link>
                  </span>
                ))}
              </li>
            </ul>
          </div>

          <div>
            <p className={headingClass}>联系与投诉</p>
            <ul className="space-y-2">
              <li>
                联系邮箱：<a href={`mailto:${CONTACT_EMAIL}`} className={`${linkClass} break-all`}>{CONTACT_EMAIL}</a>
              </li>
              {/* Report entry -- Article 15 of the "Interim Measures for the Management of Generative AI Services" requires public disclosure;
                  the footer is the most-checked location, so it must be visible site-wide with no login required (item 13 of the filing correction list). */}
              <li><a href="/report" className={linkClass}>投诉与举报</a></li>
            </ul>
          </div>

          <div>
            <p className={headingClass}>备案资质</p>
            <ul className="space-y-2">
              {ICP_BEIAN && (
                <li>
                  备案号：<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer" className={linkClass}>{ICP_BEIAN}</a>
                </li>
              )}
              {EDI_LICENSE && <li>增值电信业务经营许可证：<span className="whitespace-nowrap">{EDI_LICENSE}</span></li>}
              {POLICE_BEIAN && (
                <li>
                  <a href="https://beian.mps.gov.cn/#/query/webSearch?code=<police-beian-code>" target="_blank" rel="noopener noreferrer"
                     className={`inline-flex items-center gap-1 ${linkClass}`}>
                    <img src="/image/beian.png" alt="" width={14} height={14} className="inline-block" />
                    {POLICE_BEIAN}
                  </a>
                </li>
              )}
            </ul>
          </div>

          <div>
            <p className={headingClass}>公司信息</p>
            <ul className="space-y-2">
              <li>{COMPANY}</li>
              <li>地址：{COMPANY_ADDRESS}</li>
            </ul>
          </div>
        </div>

        <div className="mt-8 pt-6 border-t border-soft text-center text-xs text-muted">
          © 2026 {COMPANY}
        </div>
      </div>
    </footer>
  );
}
