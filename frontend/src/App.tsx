import React, { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HelmetProvider } from 'react-helmet-async';
import { Header } from './components/Header';
import { Footer } from './components/Footer';
import { ContactModalProvider } from './components/ContactModalContext';
import { ContactModal } from './components/ContactModal';
import { TierModalProvider } from './components/TierModalContext';
import { TierModal } from './components/TierModal';
import { AuthProvider } from './contexts/AuthContext';
import { AuthModalProvider, useAuthModal } from './contexts/AuthModalContext';

// Eagerly loaded: Home is the landing page
import { Home } from './pages/Home';

// Lazy loaded: everything else
const Landing = lazy(() => import('./pages/Landing').then(m => ({ default: m.Landing })));
const Result = lazy(() => import('./pages/Result').then(m => ({ default: m.Result })));
const Contact = lazy(() => import('./pages/Contact').then(m => ({ default: m.Contact })));
const GeoKnowledge = lazy(() => import('./pages/GeoKnowledge').then(m => ({ default: m.GeoKnowledge })));
const GeoKnowledgeMetrics = lazy(() => import('./pages/GeoKnowledgeMetrics').then(m => ({ default: m.GeoKnowledgeMetrics })));
const ProductsServices = lazy(() => import('./pages/ProductsServices').then(m => ({ default: m.ProductsServices })));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword').then(m => ({ default: m.ForgotPassword })));
const About = lazy(() => import('./pages/About').then(m => ({ default: m.About })));
const Advanced = lazy(() => import('./pages/Advanced').then(m => ({ default: m.Advanced })));
const CheckoutSuccess = lazy(() => import('./pages/CheckoutSuccess').then(m => ({ default: m.CheckoutSuccess })));
const CheckoutCancel = lazy(() => import('./pages/CheckoutCancel').then(m => ({ default: m.CheckoutCancel })));
const CheckoutPending = lazy(() => import('./pages/CheckoutPending').then(m => ({ default: m.CheckoutPending })));
const AccountLayout = lazy(() => import('./pages/Account/AccountLayout').then(m => ({ default: m.AccountLayout })));
const ProfileTab = lazy(() => import('./pages/Account/ProfileTab').then(m => ({ default: m.ProfileTab })));
const MembershipTab = lazy(() => import('./pages/Account/MembershipTab').then(m => ({ default: m.MembershipTab })));
const UsageTab = lazy(() => import('./pages/Account/UsageTab').then(m => ({ default: m.UsageTab })));
const HistoryTab = lazy(() => import('./pages/Account/HistoryTab').then(m => ({ default: m.HistoryTab })));
const PaymentsTab = lazy(() => import('./pages/Account/PaymentsTab').then(m => ({ default: m.PaymentsTab })));
const BrandSettingsTab = lazy(() => import('./pages/Account/BrandSettingsTab').then(m => ({ default: m.BrandSettingsTab })));
const PrivacyPolicy = lazy(() => import('./pages/PrivacyPolicy').then(m => ({ default: m.PrivacyPolicy })));
const TermsOfUse = lazy(() => import('./pages/TermsOfUse').then(m => ({ default: m.TermsOfUse })));
const CookiePolicy = lazy(() => import('./pages/CookiePolicy').then(m => ({ default: m.CookiePolicy })));
const DashboardLayout = lazy(() => import('./pages/Dashboard/DashboardLayout').then(m => ({ default: m.DashboardLayout })));
const Compose = lazy(() => import('./pages/Dashboard/Compose').then(m => ({ default: m.Compose })));
const DashboardPosts = lazy(() => import('./pages/Dashboard/Posts').then(m => ({ default: m.Posts })));
const AiTelemetry = lazy(() => import('./pages/Dashboard/AiTelemetry').then(m => ({ default: m.AiTelemetry })));
const AdminReview = lazy(() => import('./pages/Admin/Review').then(m => ({ default: m.AdminReview })));
const WorkbenchLayout = lazy(() => import('./pages/Workbench/WorkbenchLayout').then(m => ({ default: m.WorkbenchLayout })));
const Sentiment = lazy(() => import('./pages/Dashboard/Sentiment').then(m => ({ default: m.Sentiment })));
const SentimentSettings = lazy(() => import('./pages/Dashboard/sentiment/SettingsPage').then(m => ({ default: m.SettingsPage })));


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

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HelmetProvider>
      <Router>
      <AuthProvider>
      <AuthModalProvider>
      <ContactModalProvider>
      <TierModalProvider>
        {/* Outer Suspense covers Header / Footer / Modals for the brief
            moment during language switch when react-i18next flips "ready"
            state. Without this, any useTranslation component outside the
            inner route Suspense would cause the whole tree to unmount
            (white screen) when suspension bubbles past the Router. */}
        <Suspense fallback={<PageLoader />}>
        <Header />
        <ContactModal />
        <TierModal />
        <div className="pt-16 min-h-screen flex flex-col">
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/checker" element={<Home />} />
              <Route path="/geo-knowledge" element={<GeoKnowledge />} />
              <Route path="/geo-knowledge/metrics" element={<GeoKnowledgeMetrics />} />
              <Route path="/products-services" element={<ProductsServices />} />
              <Route path="/account" element={<AccountLayout />}>
                <Route index element={<Navigate to="profile" replace />} />
                <Route path="profile" element={<ProfileTab />} />
                <Route path="brand" element={<BrandSettingsTab />} />
                <Route path="membership" element={<MembershipTab />} />
                <Route path="usage" element={<UsageTab />} />
                <Route path="history" element={<HistoryTab />} />
                <Route path="payments" element={<PaymentsTab />} />
              </Route>
              <Route path="/dashboard" element={<DashboardLayout />}>
                {/* 首页 = 配置(原 AI 遥测的「话题配置」tab)
                    key 强制不同 route 间 remount,否则 tab state 会跨页串味 */}
                <Route index element={<AiTelemetry key="config" views={['config']} />} />
                <Route path="compose" element={<Compose />} />
                <Route path="posts" element={<DashboardPosts />} />
                {/* AI 遥测 = 概览 + 引用追踪 + 遥测详情 */}
                <Route path="ai-telemetry" element={<AiTelemetry key="telemetry" views={['overview', 'tracking', 'results']} />} />
                {/* 优化建议提升为顶级 */}
                <Route path="insights" element={<AiTelemetry key="insights" views={['briefings']} />} />
              </Route>
              {/* 工作台 — admin 专属,独立 sidebar 不混 AEO 菜单 */}
              <Route path="/workbench" element={<WorkbenchLayout />}>
                <Route index element={<Navigate to="review" replace />} />
                <Route path="review" element={<AdminReview />} />
              </Route>
              {/* 舆情监控独立成顶级路由,不再嵌套在 DashboardLayout 下 */}
              <Route path="/sentiment" element={<Sentiment />} />
              <Route path="/sentiment/settings" element={<SentimentSettings />} />
              <Route path="/about" element={<About />} />
              <Route path="/process" element={<Landing />} />
              <Route path="/pricing" element={<Landing />} />
              <Route path="/data" element={<Landing />} />
              <Route path="/result" element={<Result />} />
              <Route path="/advanced/:mode" element={<Advanced />} />
              <Route path="/contact" element={<Contact />} />
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
          <Footer />
        </div>
        </Suspense>
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
