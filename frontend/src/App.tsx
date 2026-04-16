import { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Header } from './components/Header';
import { Footer } from './components/Footer';
import { ContactModalProvider } from './components/ContactModalContext';
import { ContactModal } from './components/ContactModal';
import { TierModalProvider } from './components/TierModalContext';
import { TierModal } from './components/TierModal';
import './i18n';

// Eagerly loaded: Home is the landing page
import { Home } from './pages/Home';

// Lazy loaded: everything else
const Landing = lazy(() => import('./pages/Landing').then(m => ({ default: m.Landing })));
const Result = lazy(() => import('./pages/Result').then(m => ({ default: m.Result })));
const Contact = lazy(() => import('./pages/Contact').then(m => ({ default: m.Contact })));
const GeoKnowledge = lazy(() => import('./pages/GeoKnowledge').then(m => ({ default: m.GeoKnowledge })));
const GeoKnowledgeMetrics = lazy(() => import('./pages/GeoKnowledgeMetrics').then(m => ({ default: m.GeoKnowledgeMetrics })));
const ProductsServices = lazy(() => import('./pages/ProductsServices').then(m => ({ default: m.ProductsServices })));
const Login = lazy(() => import('./pages/Login').then(m => ({ default: m.Login })));
const Register = lazy(() => import('./pages/Register').then(m => ({ default: m.Register })));
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
const PrivacyPolicy = lazy(() => import('./pages/PrivacyPolicy').then(m => ({ default: m.PrivacyPolicy })));
const TermsOfUse = lazy(() => import('./pages/TermsOfUse').then(m => ({ default: m.TermsOfUse })));
const CookiePolicy = lazy(() => import('./pages/CookiePolicy').then(m => ({ default: m.CookiePolicy })));

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

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ContactModalProvider>
      <TierModalProvider>
      <Router>
        <Header />
        <ContactModal />
        <TierModal />
        <div className="pt-16">
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
                <Route path="membership" element={<MembershipTab />} />
                <Route path="usage" element={<UsageTab />} />
                <Route path="history" element={<HistoryTab />} />
                <Route path="payments" element={<PaymentsTab />} />
              </Route>
              <Route path="/about" element={<About />} />
              <Route path="/process" element={<Landing />} />
              <Route path="/pricing" element={<Landing />} />
              <Route path="/data" element={<Landing />} />
              <Route path="/result" element={<Result />} />
              <Route path="/advanced/:mode" element={<Advanced />} />
              <Route path="/contact" element={<Contact />} />
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
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
      </Router>
      </TierModalProvider>
      </ContactModalProvider>
    </QueryClientProvider>
  );
}

export default App;
