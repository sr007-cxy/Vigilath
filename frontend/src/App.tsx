import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Landing } from './pages/Landing';
import { Home } from './pages/Home';
import { Result } from './pages/Result';
import { Contact } from './pages/Contact';
import { GeoKnowledge } from './pages/GeoKnowledge';
import { GeoKnowledgeMetrics } from './pages/GeoKnowledgeMetrics';
import { ProductsServices } from './pages/ProductsServices';
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import { ForgotPassword } from './pages/ForgotPassword';
import { About } from './pages/About';
import { Advanced } from './pages/Advanced';
import { CheckoutSuccess } from './pages/CheckoutSuccess';
import { CheckoutCancel } from './pages/CheckoutCancel';
import { CheckoutPending } from './pages/CheckoutPending';
import { AccountLayout } from './pages/Account/AccountLayout';
import { ProfileTab } from './pages/Account/ProfileTab';
import { MembershipTab } from './pages/Account/MembershipTab';
import { UsageTab } from './pages/Account/UsageTab';
import { HistoryTab } from './pages/Account/HistoryTab';
import { PaymentsTab } from './pages/Account/PaymentsTab';
import { Header } from './components/Header';
import './i18n'; // 导入 i18n 配置

// Create a client
const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Header />
        <div className="pt-16">
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
          </Routes>
        </div>
      </Router>
    </QueryClientProvider>
  );
}

export default App;
