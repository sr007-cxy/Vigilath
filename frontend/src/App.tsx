import React, { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, Outlet, useParams, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HelmetProvider } from 'react-helmet-async';
import { Header } from './components/Header';
import { Footer } from './components/Footer';
import { ScrollToTop } from './components/ScrollToTop';
import { WechatSilentAuth } from './components/WechatSilentAuth';
import { ContactModalProvider } from './components/ContactModalContext';
import { ContactModal } from './components/ContactModal';
import { TierModalProvider } from './components/TierModalContext';
import { TierModal } from './components/TierModal';
import { AuthProvider, useAuth, resolveRole } from './contexts/AuthContext';
import { AgencyProvider } from './contexts/AgencyContext';
import { AuthModalProvider, useAuthModal } from './contexts/AuthModalContext';
import { AgentChatWidget } from './components/AgentChat/AgentChatWidget';
import { UiDialogHost } from './components/ui/UiDialogHost';
import { ToastHost } from './components/ui/ToastHost';
import { CommandPalette } from './components/ui/CommandPalette';
import { AgentActionsProvider } from './components/AgentChat/agentActions';
import { CtripVerificationSync } from './components/publish/CtripVerificationSync';

// Eagerly loaded: Home is the landing page
import { Home } from './pages/Home';
import { isWorkbenchPath } from './utils/workbenchPath';

// Lazy loaded: everything else
const Landing = lazy(() => import('./pages/Landing').then(m => ({ default: m.Landing })));
const Result = lazy(() => import('./pages/Result').then(m => ({ default: m.Result })));
// Print page for audit reports. Only visited by backend Chrome printing, never exposed in any navigation.
const ResultPrint = lazy(() => import('./pages/ResultPrint').then(m => ({ default: m.ResultPrint })));
const Contact = lazy(() => import('./pages/Contact').then(m => ({ default: m.Contact })));
const Report = lazy(() => import('./pages/Report').then(m => ({ default: m.Report })));
const GeoKnowledge = lazy(() => import('./pages/GeoKnowledge').then(m => ({ default: m.GeoKnowledge })));
const GeoKnowledgeMetrics = lazy(() => import('./pages/GeoKnowledgeMetrics').then(m => ({ default: m.GeoKnowledgeMetrics })));
const Faq = lazy(() => import('./pages/Faq').then(m => ({ default: m.Faq })));
const ProductsServices = lazy(() => import('./pages/ProductsServices').then(m => ({ default: m.ProductsServices })));
const Solution = lazy(() => import('./pages/Solution').then(m => ({ default: m.Solution })));
const Architecture = lazy(() => import('./pages/Architecture').then(m => ({ default: m.Architecture })));
const Onboard = lazy(() => import('./pages/Onboard'));
const DeviceLink = lazy(() => import('./pages/DeviceLink'));
const SkillInstall = lazy(() => import('./pages/SkillInstall'));
const PluginInstall = lazy(() => import('./pages/PluginInstall'));
const WecomAgent = lazy(() => import('./pages/WecomAgent'));
const SalesAgentWorkbench = lazy(() => import('./pages/SalesAgent/SalesAgentWorkbench'));
const SalesAgentPublicReport = lazy(() => import('./pages/SalesAgent/PublicReport').then(m => ({ default: m.SalesAgentPublicReport })));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword').then(m => ({ default: m.ForgotPassword })));
const About = lazy(() => import('./pages/About').then(m => ({ default: m.About })));
const Blog = lazy(() => import('./pages/Blog').then(m => ({ default: m.Blog })));
const Cases = lazy(() => import('./pages/Cases').then(m => ({ default: m.Cases })));
const CaseStudyPage = lazy(() => import('./pages/CaseStudy').then(m => ({ default: m.CaseStudyPage })));
const IndustryLibrary = lazy(() => import('./pages/Industry/IndustryLibrary').then(m => ({ default: m.IndustryLibrary })));
const IndustryDetail = lazy(() => import('./pages/Industry/IndustryDetail').then(m => ({ default: m.IndustryDetail })));
const BlogPost = lazy(() => import('./pages/BlogPost').then(m => ({ default: m.BlogPost })));
const Advanced = lazy(() => import('./pages/Advanced').then(m => ({ default: m.Advanced })));
const CheckoutSuccess = lazy(() => import('./pages/CheckoutSuccess').then(m => ({ default: m.CheckoutSuccess })));
const CheckoutCancel = lazy(() => import('./pages/CheckoutCancel').then(m => ({ default: m.CheckoutCancel })));
const CheckoutPending = lazy(() => import('./pages/CheckoutPending').then(m => ({ default: m.CheckoutPending })));
const AccountLayout = lazy(() => import('./pages/Account/AccountLayout').then(m => ({ default: m.AccountLayout })));
const ProfileTab = lazy(() => import('./pages/Account/ProfileTab').then(m => ({ default: m.ProfileTab })));
const BillingTab = lazy(() => import('./pages/Account/BillingTab').then(m => ({ default: m.BillingTab })));
const IntegrationsTab = lazy(() => import('./pages/Account/IntegrationsTab').then(m => ({ default: m.IntegrationsTab })));
const HistoryTab = lazy(() => import('./pages/Account/HistoryTab').then(m => ({ default: m.HistoryTab })));
const AgencyTab = lazy(() => import('./pages/Account/AgencyTab').then(m => ({ default: m.AgencyTab })));
const PrivacyPolicy = lazy(() => import('./pages/PrivacyPolicy').then(m => ({ default: m.PrivacyPolicy })));
const TermsOfUse = lazy(() => import('./pages/TermsOfUse').then(m => ({ default: m.TermsOfUse })));
const CookiePolicy = lazy(() => import('./pages/CookiePolicy').then(m => ({ default: m.CookiePolicy })));
const DiagnosisHome = lazy(() => import('./pages/Diagnosis').then(m => ({ default: m.DiagnosisHome })));
const DiagnosisReport = lazy(() => import('./pages/Diagnosis/Report').then(m => ({ default: m.DiagnosisReport })));
const CustomerTopics = lazy(() => import('./pages/Workbench/CustomerTopics').then(m => ({ default: m.CustomerTopics })));
const AgencyEnterprises = lazy(() => import('./pages/Workbench/AgencyEnterprises').then(m => ({ default: m.AgencyEnterprises })));
const Content = lazy(() => import('./pages/Dashboard/Content').then(m => ({ default: m.Content })));
const PublishingTab = lazy(() => import('./pages/Account/PublishingTab').then(m => ({ default: m.PublishingTab })));
const BrandSettingsTab = lazy(() => import('./pages/Account/BrandSettingsTab').then(m => ({ default: m.BrandSettingsTab })));
const SalesAgentEmailRecordsPage = lazy(() => import('./pages/Workbench/SalesAgentEmailRecordsPage').then(m => ({ default: m.SalesAgentEmailRecordsPage })));
const SalesAgentSmsRecordsPage = lazy(() => import('./pages/Workbench/SalesAgentSmsRecordsPage').then(m => ({ default: m.SalesAgentSmsRecordsPage })));
const ReportsCompliance = lazy(() => import('./pages/Reports/Compliance').then(m => ({ default: m.Compliance })));
const ReportsOverview = lazy(() => import('./pages/Reports/Overview').then(m => ({ default: m.ReportsOverview })));
const ReportList = lazy(() => import('./pages/Reports/ReportList').then(m => ({ default: m.ReportList })));
const DailyReport = lazy(() => import('./pages/Reports/DailyReport').then(m => ({ default: m.DailyReport })));
const WeeklyReport = lazy(() => import('./pages/Reports/WeeklyReport').then(m => ({ default: m.WeeklyReport })));
const MonthlyReport = lazy(() => import('./pages/Reports/MonthlyReport').then(m => ({ default: m.MonthlyReport })));
const TaskMonitor = lazy(() => import('./pages/Reports/TaskMonitor').then(m => ({ default: m.TaskMonitor })));
const TaskMonitorRun = lazy(() => import('./pages/Reports/TaskMonitorRun').then(m => ({ default: m.TaskMonitorRun })));
const AdminContentReview = lazy(() => import('./pages/Admin/ContentReview').then(m => ({ default: m.AdminContentReview })));
const AdminContentReviewDetail = lazy(() => import('./pages/Admin/ContentReviewDetail').then(m => ({ default: m.AdminContentReviewDetail })));
const AdminContentReviewPublish = lazy(() => import('./pages/Admin/ContentReviewPublish').then(m => ({ default: m.AdminContentReviewPublish })));
const AdminContentTrace = lazy(() => import('./pages/Admin/ContentTrace').then(m => ({ default: m.AdminContentTrace })));
const AdminMediaLibrary = lazy(() => import('./pages/Admin/MediaLibrary').then(m => ({ default: m.AdminMediaLibrary })));
const AdminAbuseReports = lazy(() => import('./pages/Admin/AbuseReports').then(m => ({ default: m.AdminAbuseReports })));
const AdminPlatformRules = lazy(() => import('./pages/Admin/PlatformRules').then(m => ({ default: m.AdminPlatformRules })));
const AccountCenter = lazy(() => import('./pages/Admin/AccountCenter').then(m => ({ default: m.AccountCenter })));
const AdminAccounts = lazy(() => import('./pages/Workbench/AdminAccounts').then(m => ({ default: m.AdminAccounts })));
const AdminAccountTopics = lazy(() => import('./pages/Workbench/AdminAccountTopics').then(m => ({ default: m.AdminAccountTopics })));
const AdminTopicEdit = lazy(() => import('./pages/Workbench/AdminTopicEdit').then(m => ({ default: m.AdminTopicEdit })));
const AdminAllRuns = lazy(() => import('./pages/Workbench/AdminAllRuns').then(m => ({ default: m.AdminAllRuns })));
const AdminRunDetail = lazy(() => import('./pages/Workbench/AdminRunDetail').then(m => ({ default: m.AdminRunDetail })));
const AdminCockpit = lazy(() => import('./pages/Workbench/AdminCockpit').then(m => ({ default: m.AdminCockpit })));
const AdminInsights = lazy(() => import('./pages/Workbench/AdminInsights').then(m => ({ default: m.AdminInsights })));
const AdminSchedule = lazy(() => import('./pages/Workbench/AdminSchedule').then(m => ({ default: m.AdminSchedule })));
const AdminActionItems = lazy(() => import('./pages/Workbench/AdminActionItems').then(m => ({ default: m.AdminActionItems })));
const AdminCrawl = lazy(() => import('./pages/Workbench/AdminCrawl').then(m => ({ default: m.AdminCrawl })));
const AdminProbe = lazy(() => import('./pages/Workbench/AdminProbe').then(m => ({ default: m.AdminProbe })));
const AdminWorkers = lazy(() => import('./pages/Workbench/AdminWorkers').then(m => ({ default: m.AdminWorkers })));
const AdminGateway = lazy(() => import('./pages/Workbench/AdminGateway').then(m => ({ default: m.AdminGateway })));
// Login entry for the collection network console (its login page's "log in with Vigilath account" points here): log in -> get key -> redirect over
const VnetConsole = lazy(() => import('./pages/Workbench/VnetConsole').then(m => ({ default: m.VnetConsole })));
const UserOrganizationManagement = lazy(() => import('./pages/Workbench/UserOrganizationManagement').then(m => ({ default: m.UserOrganizationManagement })));
const AdminLlmUsage = lazy(() => import('./pages/Workbench/AdminLlmUsage').then(m => ({ default: m.AdminLlmUsage })));
const AdminCallPolicy = lazy(() => import('./pages/Workbench/AdminCallPolicy').then(m => ({ default: m.AdminCallPolicy })));
const AdminSalesAgentEmail = lazy(() => import('./pages/Workbench/AdminSalesAgentEmail').then(m => ({ default: m.AdminSalesAgentEmail })));
const AdminSalesAgentSms = lazy(() => import('./pages/Workbench/AdminSalesAgentSms').then(m => ({ default: m.AdminSalesAgentSms })));
const SalesAgentWorkbenchPage = lazy(() => import('./pages/Workbench/SalesAgentWorkbenchPage').then(m => ({ default: m.SalesAgentWorkbenchPage })));
const GatewayPortal = lazy(() => import('./pages/GatewayPortal/GatewayPortal').then(m => ({ default: m.GatewayPortal })));
const TopicProfile = lazy(() => import('./pages/User/TopicProfile').then(m => ({ default: m.TopicProfile })));
const OpsLayout = lazy(() => import('./pages/Workbench/OpsLayout').then(m => ({ default: m.OpsLayout })));
const OpsIndexRedirect = lazy(() => import('./pages/Workbench/OpsLayout').then(m => ({ default: m.OpsIndexRedirect })));
const AppLayout = lazy(() => import('./pages/App/AppLayout').then(m => ({ default: m.AppLayout })));
const AppIndexRedirect = lazy(() => import('./pages/App/AppLayout').then(m => ({ default: m.AppIndexRedirect })));
const Sentiment = lazy(() => import('./pages/Dashboard/Sentiment').then(m => ({ default: m.Sentiment })));


function PageLoader() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div
        className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2"
        style={{ borderColor: 'var(--accent-primary)' }}
      />
    </div>
  );
}

const queryClient = new QueryClient();

/** Visit /login or /register → redirect home + open auth modal. */
function LoginRedirect({ tab }: { tab: 'login' | 'register' }) {
  const { openAuthModal } = useAuthModal();
  // Defer to next tick so the route renders first, then opens modal
  React.useEffect(() => {
    openAuthModal(tab);
  }, [tab, openAuthModal]);
  return <Navigate to="/" replace />;
}

/** Old single-topic detail routes (/solution /execution-plan /docs) were merged into the "profile" main flow; redirect directly to the matching step. */
function TopicEditRedirect({ step }: { step: number }) {
  const { topicId } = useParams();
  return <Navigate to={`/ops/topics/${topicId}/edit?step=${step}`} replace />;
}

/**
 * /workbench/* fallback -> /ops/*. Paths whose destination changed are listed one by one above; this only moves the remaining same-named pages.
 * The splat is read via useParams()['*'], preserving the original query.
 */
function WorkbenchToOpsRedirect() {
  const rest = useParams()['*'] || '';
  const loc = useLocation();
  return <Navigate to={`/ops/${rest}${loc.search}`} replace />;
}

/** "Users & organizations" routed by role: ops -> /ops/users, agency admin -> /app/members. */
function WorkbenchUsersRedirect() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  const staff = role === 'admin' || role === 'operator' || role === 'maintainer';
  return <Navigate to={staff ? '/ops/users' : '/app/members'} replace />;
}

/**
 * "Optimization tasks" used to serve two roles on one path (customer self-service / ops across customers); after the split the destinations differ:
 * customers go to the tasks tab of /app/content, ops stay on /ops/action-items.
 */
function WorkbenchActionItemsRedirect() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  const staff = role === 'admin' || role === 'operator';
  return <Navigate to={staff ? '/ops/action-items' : '/app/action-items'} replace />;
}

/** Path redirect that keeps the query (preserves params like ?topic=). */
function RedirectKeepQuery({ to, hash = '' }: { to: string; hash?: string }) {
  const loc = useLocation();
  return <Navigate to={`${to}${loc.search}${hash || loc.hash}`} replace />;
}

/** Gateway tenants only have the tenant portal: any regular user page redirects to /gateway-portal. */
function NonTenantOnly() {
  const { isTenant } = useAuth();
  if (isTenant) return <Navigate to="/gateway-portal" replace />;
  return <Outlet />;
}

// /wecom (WeCom embedded page), /sales-agent (hidden workbench), /g (field-promotion landing page) must be pure full-screen,
// without the site-wide Header/Footer/top padding; other routes get the chrome as usual.
function Chrome({ children }: { children: React.ReactNode }) {
  const pathname = useLocation().pathname;
  // /result/print/* is the paper document for backend Chrome printing; any site chrome would be printed into the PDF
  const bare = pathname.startsWith('/wecom') || pathname.startsWith('/sales-agent')
    || pathname.startsWith('/result/print');
  if (bare) return <>{children}</>;
  // App shell: the workbench page itself doesn't scroll; the UI is as tall as the window, and scrolling is left to the content area.
  // Admin lists in document flow would stretch the whole page -- headers scroll away, pagination is only visible at the bottom, the sidebar drifts off.
  // The height chain starts here: h-screen + pt-16 (border-box) -> the content box is exactly the viewport minus the top bar.
  // Spec in docs/03-系统设计/前端UI规范.md "Layout patterns".
  const shell = isWorkbenchPath(pathname);
  return (
    <>
      <Header />
      <ContactModal />
      <TierModal />
      {/* --agent-dock-w: when the agent chat window is docked on the right, the content area yields in sync -- **push aside, don't cover**.
          The variable is set on documentElement by AgentChatWidget and cleared automatically when collapsed (default 0).
          Placed here as the **single yield point**: previously only WorkbenchLayout read it, so other pages got covered;
          writing it in both places would double-apply. Bare branches (/wecom, /sales-agent, /result/print) don't go through here and are naturally unaffected. */}
      <div
        className={shell
          ? 'pt-16 h-screen overflow-hidden flex flex-col'
          : 'pt-16 min-h-screen flex flex-col'}
        style={{ paddingRight: 'var(--agent-dock-w, 0px)', transition: 'padding-right .18s ease' }}
      >
        {children}
        {/* The app shell has no footer -- the page doesn't scroll, so the footer would never be visible and just waste a strip of height */}
        {!shell && <Footer />}
      </div>
    </>
  );
}

/**
 * Never rendered on print routes (/result/print/*).
 *
 * `position:fixed` elements like the support bubble get **printed on every page** by Chrome.
 * The backend `_DOC_PREPARE_JS` adds another safety net (hiding everything outside the document area); here we keep
 * them out of the DOM -- which also saves a few pointless requests during printing.
 */
function NonPrint({ children }: { children: React.ReactNode }) {
  return useLocation().pathname.startsWith('/result/print') ? null : <>{children}</>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HelmetProvider>
      <Router>
      <AuthProvider>
      <AuthModalProvider>
      <ContactModalProvider>
      <TierModalProvider>
      <AgentActionsProvider>
      <AgencyProvider>
        {/* Outer Suspense covers Header / Footer / Modals for the brief
            moment during language switch when react-i18next flips "ready"
            state. Without this, any useTranslation component outside the
            inner route Suspense would cause the whole tree to unmount
            (white screen) when suspension bubbles past the Router. */}
        <Suspense fallback={<PageLoader />}>
        <ScrollToTop />
        {/* Site-wide WeChat silent login: on any page inside WeChat, if not logged in, silently identify the user (zero-click login for returning users).
            Does nothing outside WeChat / when logged in / when the channel isn't configured. See comments in the component. */}
        <WechatSilentAuth />
        <Chrome>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/link" element={<DeviceLink />} />
              <Route path="/install" element={<SkillInstall />} />
              <Route path="/plugin" element={<PluginInstall />} />
              <Route path="/checker" element={<Home />} />
              <Route path="/geo-knowledge" element={<GeoKnowledge />} />
              <Route path="/geo-knowledge/metrics" element={<GeoKnowledgeMetrics />} />
              <Route path="/geo-knowledge/faq" element={<Navigate to="/faq" replace />} />
              <Route path="/faq" element={<Faq />} />
              <Route path="/products-services" element={<ProductsServices />} />
              <Route path="/solutions/architecture" element={<Architecture />} />
              <Route path="/solutions/:slug" element={<Solution />} />
              <Route path="/onboard" element={<Onboard />} />
              <Route path="/vnet-console" element={<VnetConsole />} />
              {/* One-click full audit: moved out of the workbench into a standalone page (with site Header/Footer, no sidebar) */}
              <Route path="/app/diagnosis" element={<DiagnosisHome />} />
              {/* The WeChat official account menu "My reports" is set to /app/reports (scripts/setup_wechat_menu.py).
                  After the 09-08 sidebar refactor moved the batch run report center from /app/reports to /reports, this
                  address landed in the /app shell without any child route matching -- shell rendered, content area blank, so field-promotion users
                  tapping "My reports" in the official account saw a white page. For field-promotion users "reports" means the one-click diagnosis
                  history list, so send them straight there and scroll to the history section; the menu takes 24h to apply to existing users,
                  while this redirect takes effect immediately. */}
              <Route path="/app/reports" element={<RedirectKeepQuery to="/app/diagnosis" hash="#history" />} />
              <Route path="/link" element={<DeviceLink />} />
              <Route path="/install" element={<SkillInstall />} />
              <Route path="/plugin" element={<PluginInstall />} />
              <Route path="/wecom" element={<WecomAgent />} />
              <Route path="/sales-agent" element={<SalesAgentWorkbench />} />
              <Route path="/sales-agent/report/:token" element={<SalesAgentPublicReport />} />
              {/* Regular user area: gateway tenants (role=tenant) always go to the tenant portal */}
              <Route element={<NonTenantOnly />}>
              <Route path="/account" element={<AccountLayout />}>
                <Route index element={<Navigate to="profile" replace />} />
                <Route path="profile" element={<ProfileTab />} />
                {/* Membership + usage + credits + payment combined into one page with four tabs */}
                <Route path="billing" element={<BillingTab />} />
                {/* Integrations + email config + SMS config combined into one page with three tabs */}
                <Route path="integrations" element={<IntegrationsTab />} />
                <Route path="agency" element={<AgencyTab />} />
                {/* Complaints & reports: the menu item used to point directly to the top-level /report, which kicked users out of the account center
                    (the whole sidebar disappeared). The same Report component is mounted again here, keeping the shell;
                    the top-level /report must be kept as-is and login-free -- Article 15 of the "Interim Measures" requires public disclosure. */}
                <Route path="report" element={<Report embedded />} />
                {/* When the balance is insufficient, the backend's 402 points here; don't change the path -- redirects to the credits tab of the billing page */}
                <Route path="balance" element={<Navigate to="/account/billing?tab=balance" replace />} />
                {/* Pre-merge old paths: all redirect to the new destinations, no 404 */}
                <Route path="membership" element={<Navigate to="/account/billing?tab=membership" replace />} />
                <Route path="usage" element={<Navigate to="/account/billing?tab=usage" replace />} />
                <Route path="payments" element={<Navigate to="/account/billing?tab=payments" replace />} />
                <Route path="integration" element={<Navigate to="/account/integrations?tab=agent" replace />} />
                <Route path="sales-agent-email" element={<Navigate to="/account/integrations?tab=email" replace />} />
                <Route path="sales-agent-sms" element={<Navigate to="/account/integrations?tab=sms" replace />} />
                {/* Three business data items + one business config item moved to /app */}
                <Route path="articles" element={<Navigate to="/app/content" replace />} />
                <Route path="publishing" element={<Navigate to="/app/publishing" replace />} />
                <Route path="history" element={<Navigate to="/app/history" replace />} />
                <Route path="brand" element={<Navigate to="/app/sentiment/settings" replace />} />
              </Route>
              <Route path="/dashboard/topics/:topicId/profile" element={<TopicProfile />} />

              {/* ── Customer workbench /app -- sidebar shell, menu source constants/userNav.ts ──
                  Every item is one click away in the sidebar; no more piling several features into one menu item via tabs.
                  Padding is provided exclusively by SidebarShell's main; pages never add their own -- previously the customer side
                  wrapped in Padded while the ops side had nothing, so one floated centered and the other hugged the menu. */}
              <Route path="/app" element={<AppLayout />}>
                <Route index element={<AppIndexRedirect />} />
                {/* Projects */}
                <Route path="topics" element={<CustomerTopics />} />
                <Route path="topics/:topicId/profile" element={<TopicProfile />} />
                <Route path="action-items" element={<AdminActionItems />} />
                <Route path="content" element={<Content />} />
                {/* The placement page uses the "list page" skeleton (h-full flex flex-col): it owns height, the shell owns padding */}
                <Route path="publishing" element={<PublishingTab />} />
                <Route path="history" element={<HistoryTab />} />
                {/* Monitoring */}
                <Route path="sentiment" element={<Sentiment />} />
                {/* Sentiment settings = the old account center's "Brand & monitoring". It always was the sentiment monitoring settings page
                    (brand name/aliases · keyword groups · notification email · brand tone and legal red lines);
                    the component is still called BrandSettingsTab for historical reasons. Here the real component is rendered directly --
                    previously it was forwarded via pages/Dashboard/sentiment/SettingsPage, a 7-line empty shell
                    redirecting to /account/brand, and after the account center was slimmed /account/brand redirected to
                    /app/topics, so clicking "Sentiment settings" dumped you on the topic profile page. */}
                <Route path="sentiment/settings" element={<BrandSettingsTab />} />
                {/* Outreach */}
                <Route path="email-records" element={<SalesAgentEmailRecordsPage />} />
                <Route path="sms-records" element={<SalesAgentSmsRecordsPage />} />
                {/* Settings */}
                <Route path="brand" element={<Navigate to="/app/sentiment/settings" replace />} />
                <Route path="enterprises" element={<AgencyEnterprises />} />
                {/* Agencies manage their own organization's members -- the same component as /ops/users,
                    differing in data scope (narrowed by role inside the component; the backend is still the final boundary).
                    When the shells were split this page was left only in the admin workbench, which isn't open to agency_admin. */}
                <Route path="members" element={<UserOrganizationManagement />} />
              </Route>

              {/* ── Report center -- a standalone module, same as before ──
                  It has its own ReportsShell (title / topic picker / module nav / sticky header);
                  wrapped in the workbench shell it would get two headers, so it stays a top-level route in document flow.
                  The "Report center" item in the workbench sidebar has a ↗; clicking it leaves the shell. */}
              <Route path="/reports" element={<ReportsOverview />} />
              <Route path="/reports/daily" element={<ReportList periodType="daily" />} />
              <Route path="/reports/weekly" element={<ReportList periodType="weekly" />} />
              <Route path="/reports/monthly" element={<ReportList periodType="monthly" />} />
              <Route path="/reports/daily/:day" element={<DailyReport />} />
              <Route path="/reports/weekly/:weekKey" element={<WeeklyReport />} />
              <Route path="/reports/monthly/:monthKey" element={<MonthlyReport />} />
              <Route path="/reports/compliance" element={<ReportsCompliance />} />
              <Route path="/task-monitor" element={<TaskMonitor />} />
              <Route path="/task-monitor/runs/:runId" element={<TaskMonitorRun />} />
              {/* Metrics dashboard / evidence center are retired; old links fall back to the report overview */}
              <Route path="/reports/metrics" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/reports/evidence" element={<RedirectKeepQuery to="/reports" />} />

              {/* The diagnosis report page stays top-level -- it's a standalone shareable document, not wrapped in /app navigation */}
              <Route path="/diagnosis/:id" element={<DiagnosisReport />} />

              {/* ── Old path redirects: never 404 ──
                  Brand growth (entry hidden 2026-07-14, superseded by the report center) is retired as a group,
                  each mapped by capability to the closest destination in the report center. */}
              <Route path="/brand-growth" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/competitors" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/matrix" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/insights" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/sources" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/source-strategy" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/queries" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/responses" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/engines" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/published" element={<RedirectKeepQuery to="/app/publishing" />} />
              <Route path="/brand-growth/compliance" element={<RedirectKeepQuery to="/reports/compliance" />} />
              <Route path="/brand-growth/reports" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/metrics" element={<RedirectKeepQuery to="/reports" />} />
              <Route path="/brand-growth/evidence" element={<RedirectKeepQuery to="/reports" />} />
              {/* Dashboard shell retired: all four of its sidebar items pointed to routes that were redirects anyway,
                  so the sidebar could never keep a selected state -- the shell is removed and routes redirect directly. */}
              <Route path="/dashboard" element={<Navigate to="/app" replace />} />
              <Route path="/dashboard/content" element={<Navigate to="/app/content" replace />} />
              <Route path="/dashboard/compose" element={<Navigate to="/app/content" replace />} />
              <Route path="/dashboard/posts" element={<Navigate to="/app/publishing" replace />} />
              <Route path="/dashboard/ai-telemetry" element={<Navigate to="/reports" replace />} />
              <Route path="/dashboard/insights" element={<Navigate to="/app/action-items" replace />} />
              {/* Sentiment monitoring moved from a top-level island into the workbench sidebar */}
              <Route path="/sentiment" element={<RedirectKeepQuery to="/app/sentiment" />} />
              <Route path="/sentiment/settings" element={<RedirectKeepQuery to="/app/sentiment/settings" />} />
              {/* One-click full diagnosis merged into the workbench "Audit" group; the report page is still a standalone shareable document */}
              <Route path="/diagnosis" element={<Navigate to="/app/diagnosis" replace />} />

              {/* ── Admin workbench /ops -- admin / operator / maintainer, menu source constants/opsNav.ts ──
                  The three customer items (topics / content library / optimization tasks) moved to /app/content and no longer appear here. */}
              <Route path="/ops" element={<OpsLayout />}>
                <Route index element={<OpsIndexRedirect />} />
                {/* Delivery */}
                <Route path="cockpit" element={<AdminCockpit />} />
                <Route path="accounts" element={<AdminAccounts />} />
                <Route path="accounts/:userId/topics" element={<AdminAccountTopics />} />
                <Route path="insights" element={<AdminInsights />} />
                <Route path="action-items" element={<AdminActionItems />} />
                {/* Content governance */}
                <Route path="content-review" element={<AdminContentReview />} />
                <Route path="content-review/:docId" element={<AdminContentReviewDetail />} />
                <Route path="content-review/:docId/publish" element={<AdminContentReviewPublish />} />
                {/* Generation provenance -- reverse lookup of implicit labels per the "Labeling Measures" (item 5 of the filing correction list) */}
                <Route path="content-trace" element={<AdminContentTrace />} />
                {/* Asset library -- image overview + content labels + external check results (image side of correction list items 9/10) */}
                <Route path="media" element={<AdminMediaLibrary />} />
                {/* Report handling -- admin side of item 13 of the filing correction list */}
                <Route path="abuse-reports" element={<AdminAbuseReports />} />
                <Route path="platform-rules" element={<AdminPlatformRules />} />
                {/* Publish account pool -- the old name "Account center" clashed in Chinese with "Customer accounts", so it was renamed */}
                <Route path="publish-accounts" element={<AccountCenter />} />
                {/* Operations */}
                <Route path="runs" element={<AdminAllRuns />} />
                <Route path="runs/:runId" element={<AdminRunDetail />} />
                <Route path="schedule" element={<AdminSchedule />} />
                <Route path="crawl" element={<AdminCrawl />} />
                <Route path="probe" element={<AdminProbe />} />
                <Route path="workers" element={<AdminWorkers />} />
                <Route path="gateway" element={<AdminGateway />} />
                {/* Platform */}
                <Route path="users" element={<UserOrganizationManagement />} />
                <Route path="llm-usage" element={<AdminLlmUsage />} />
                {/* Call management -- unified call tiers + call logs for internal and external use */}
                <Route path="call-policies" element={<AdminCallPolicy />} />
                <Route path="sales-agent" element={<SalesAgentWorkbenchPage />} />
                <Route path="sales-agent-email" element={<AdminSalesAgentEmail />} />
                <Route path="sales-agent-sms" element={<AdminSalesAgentSms />} />
                {/* Customer topic editing wizard -- entered from "Customer accounts -> Topics", not a top-level menu item */}
                <Route path="topics/:topicId/edit" element={<AdminTopicEdit />} />
                <Route path="topics/:topicId/solution" element={<TopicEditRedirect step={4} />} />
                <Route path="topics/:topicId/execution-plan" element={<TopicEditRedirect step={5} />} />
                <Route path="topics/:topicId/docs" element={<TopicEditRedirect step={6} />} />
              </Route>

              {/* /workbench retired as a whole -> /ops. The word "workbench" was used by both the customer side and the ops side,
                  and was split apart by renaming; on 2026-09-11 the ops side was named "Admin workbench" --
                  so both sides say "workbench" again, but their route prefixes (/app and /ops) are separate,
                  no longer the original one-word-two-places situation. Old bookmarks never 404. */}
              <Route path="/workbench" element={<Navigate to="/ops" replace />} />
              <Route path="/workbench/approvals" element={<Navigate to="/ops/accounts" replace />} />
              <Route path="/workbench/review" element={<Navigate to="/ops/accounts" replace />} />
              <Route path="/workbench/account-center" element={<Navigate to="/ops/publish-accounts" replace />} />
              <Route path="/workbench/reports" element={<Navigate to="/ops/abuse-reports" replace />} />
              {/* The three customer items moved to tabs of /app/content */}
              <Route path="/workbench/topics" element={<Navigate to="/app/topics" replace />} />
              <Route path="/workbench/content-management" element={<Navigate to="/app/content" replace />} />
              <Route path="/workbench/action-items" element={<WorkbenchActionItemsRedirect />} />
              <Route path="/workbench/enterprises" element={<Navigate to="/app/enterprises" replace />} />
              {/* "Users & organizations" used to serve two roles on one path; after splitting the shells the destinations differ:
                  ops stay on /ops/users, agency admins go to /app/members.
                  Without routing by role, an agency_admin's old bookmark would fall back to /ops/* and be bounced by the admin workbench. */}
              <Route path="/workbench/users" element={<WorkbenchUsersRedirect />} />
              <Route path="/workbench/sales-agent-email-records" element={<Navigate to="/app/email-records" replace />} />
              <Route path="/workbench/sales-agent-sms-records" element={<Navigate to="/app/sms-records" replace />} />
              {/* Remaining /workbench/* moved as-is to /ops/* */}
              <Route path="/workbench/*" element={<WorkbenchToOpsRedirect />} />
              </Route>
              {/* Tenant portal -- self-service lookup page after a gateway tenant logs in */}
              <Route path="/gateway-portal" element={<GatewayPortal />} />
              <Route path="/about" element={<About />} />
              <Route path="/blog" element={<Blog />} />
              <Route path="/cases" element={<Cases />} />
              <Route path="/cases/:slug" element={<CaseStudyPage />} />
              {/* Industry library -- public page, no login required (data aggregated from category terms of whitelisted industries) */}
              <Route path="/industry" element={<IndustryLibrary />} />
              <Route path="/industry/:key" element={<IndustryDetail />} />
              <Route path="/blog/:slug" element={<BlogPost />} />
              <Route path="/process" element={<Landing />} />
              <Route path="/pricing" element={<Landing />} />
              <Route path="/data" element={<Landing />} />
              <Route path="/result" element={<Result />} />
              <Route path="/result/print/:recordId" element={<ResultPrint />} />
              <Route path="/advanced/:mode" element={<Advanced />} />
              <Route path="/contact" element={<Contact />} />
              {/* Complaints & reports -- public page, **no login required** (item 13 of the filing correction list) */}
              <Route path="/report" element={<Report />} />
              <Route path="/login" element={<LoginRedirect tab="login" />} />
              <Route path="/register" element={<LoginRedirect tab="register" />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/checkout/pending" element={<CheckoutPending />} />
              <Route path="/checkout/success" element={<CheckoutSuccess />} />
              <Route path="/checkout/cancel" element={<CheckoutCancel />} />
              <Route path="/privacy" element={<PrivacyPolicy />} />
              <Route path="/terms" element={<TermsOfUse />} />
              <Route path="/cookie-policy" element={<CookiePolicy />} />
            </Routes>
          </Suspense>
        </Chrome>
        <NonPrint>
          <AgentChatWidget />
          <UiDialogHost />
          <ToastHost />
          <CtripVerificationSync />
          {/* ⌘K command palette: attaches its own global keydown, renders no DOM normally */}
          <CommandPalette />
        </NonPrint>
        </Suspense>
      </AgencyProvider>
      </AgentActionsProvider>
      </TierModalProvider>
      </ContactModalProvider>
      </AuthModalProvider>
      </AuthProvider>
      </Router>
      </HelmetProvider>
    </QueryClientProvider>
  );
}

export default App;
