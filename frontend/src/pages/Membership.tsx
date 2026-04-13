import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { membershipApi } from '../services/membershipApi';
import { authApi } from '../services/authApi';

// Define types locally to avoid import issues
interface MembershipType {
  id: number;
  name: string;
  price: number;
  period: string;
  description: string;
  popular: boolean;
  features: string[];
  not_included: string[];
  created_at: string;
  updated_at: string;
}

interface UserMembership {
  id: number;
  user_id: number;
  membership_id: number;
  start_date: string;
  end_date: string;
  is_active: boolean;
}

interface User {
  id: number;
  email: string;
  name: string;
  is_active: boolean;
}

export function Membership() {
  // const { t } = useTranslation();
  const [memberships, setMemberships] = useState<MembershipType[]>([]);
  const [userMembership, setUserMembership] = useState<UserMembership | null>(null);
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  // const [userMembershipLoading, setUserMembershipLoading] = useState(false);
  // const [userMembershipError, setUserMembershipError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    const fetchMemberships = async () => {
      try {
        const data = await membershipApi.getMemberships();
        setMemberships(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch memberships');
      } finally {
        setIsLoading(false);
      }
    };

    fetchMemberships();
  }, []);

  useEffect(() => {
    const fetchUserMembership = async () => {
      const token = localStorage.getItem('token');
      if (!token) {
        setIsLoading(false);
        return;
      }

      try {
        // 获取当前用户信息
        const userData = await authApi.getCurrentUser(token);
        setCurrentUser(userData);
        
        // 获取用户会员信息
        const membershipData = await membershipApi.getUserMembership(token);
        setUserMembership(membershipData);
      } catch (err) {
        console.error('Failed to fetch user membership:', err);
      } finally {
        setIsLoading(false);
      }
    };

    fetchUserMembership();
  }, []);

  const handleSelectPlan = (membershipId: number) => {
    const token = localStorage.getItem('token');
    if (!token) {
      // 未登录，跳转到登录页
      navigate('/login');
      return;
    }

    // 这里应该实现支付流程
    // 简化处理，直接调用升级会员API
    const upgradeMembership = async () => {
      try {
        if (!currentUser) {
          throw new Error('User information not available');
        }
        
        await membershipApi.upgradeMembership(token, membershipId);
        alert('会员升级成功！');
        // 刷新页面以显示最新的会员状态
        window.location.reload();
      } catch (err) {
        alert('会员升级失败：' + (err instanceof Error ? err.message : 'Unknown error'));
      }
    };

    upgradeMembership();
  };

  const handleCancelMembership = async () => {
    const token = localStorage.getItem('token');
    if (!token) {
      navigate('/login');
      return;
    }

    if (window.confirm('确定要取消会员吗？')) {
      try {
        await membershipApi.cancelMembership(token);
        alert('会员已取消！');
        // 刷新页面以显示最新的会员状态
        window.location.reload();
      } catch (err) {
        alert('取消会员失败：' + (err instanceof Error ? err.message : 'Unknown error'));
      }
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-500 mx-auto mb-4"></div>
          <p>加载中...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-500 mb-4">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="bg-blue-500 text-white px-4 py-2 rounded"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white min-h-screen">
      {/* Hero Section */}
      <section className="relative overflow-hidden bg-gradient-to-r from-blue-600 to-blue-800 text-white py-20 md:py-28">
        <div className="max-w-6xl mx-auto px-6 relative z-10">
          <div className="max-w-3xl">
            <h1 className="text-3xl md:text-4xl lg:text-5xl font-bold mb-6">
              会员服务
            </h1>
            <p className="text-lg md:text-xl text-blue-100 mb-8">
              选择适合您需求的会员套餐，获得更全面的GEO检测和优化服务。
            </p>
          </div>
        </div>
      </section>

      {/* User Membership Status */}
      {currentUser && (
        <section className="py-6 bg-blue-50 border-b border-blue-100">
          <div className="max-w-6xl mx-auto px-6">
            <div className="flex flex-col md:flex-row justify-between items-center">
              <div>
                <h2 className="text-xl font-semibold text-gray-900">欢迎，{currentUser.name}</h2>
                {userMembership ? (
                  <p className="text-gray-600">
                    当前会员：{memberships.find(m => m.id === userMembership.membership_id)?.name}
                    {userMembership.is_active ? ' (活跃)' : ' (已过期)'}
                    {userMembership.end_date && `，到期时间：${new Date(userMembership.end_date).toLocaleDateString()}`}
                  </p>
                ) : (
                  <p className="text-gray-600">您当前没有会员资格</p>
                )}
              </div>
              {userMembership && userMembership.is_active && (
                <button
                  onClick={handleCancelMembership}
                  className="mt-4 md:mt-0 px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 transition-colors"
                >
                  取消会员
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Membership Tiers */}
      <section className="py-20 bg-gray-50">
        <div className="max-w-6xl mx-auto px-6">
          <h2 className="text-3xl font-bold text-gray-900 mb-4 text-center">
            会员套餐
          </h2>
          <p className="text-gray-600 max-w-2xl mx-auto text-center mb-16">
            选择最适合您需求的会员套餐，享受更全面的GEO检测和优化服务。
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
            {memberships.map((tier) => (
              <div
                key={tier.id}
                className={`relative bg-white rounded-2xl ${tier.popular ? 'border border-blue-500 shadow-xl' : 'shadow-sm'} p-8 hover:shadow-md transition-all duration-300`}
              >
                {tier.popular && (
                  <div className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1 bg-blue-600 text-white text-sm font-semibold rounded-full">
                    推荐
                  </div>
                )}
                {userMembership && userMembership.membership_id === tier.id && userMembership.is_active && (
                  <div className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1 bg-green-600 text-white text-sm font-semibold rounded-full">
                    当前套餐
                  </div>
                )}
                <h3 className="text-xl font-bold text-gray-900 mb-2">{tier.name}</h3>
                <div className="mb-4">
                  <span className="text-4xl font-bold text-gray-900">¥{tier.price}</span>
                  <span className="text-gray-500">{tier.period}</span>
                </div>
                <p className="text-gray-600 mb-6">{tier.description}</p>
                <ul className="space-y-3 mb-8">
                  {tier.features.map((feature, index) => (
                    <li key={index} className="flex items-start gap-3">
                      <svg className="w-5 h-5 text-green-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path>
                      </svg>
                      <span className="text-gray-700">{feature}</span>
                    </li>
                  ))}
                  {tier.not_included.map((feature, index) => (
                    <li key={index} className="flex items-start gap-3 opacity-50">
                      <svg className="w-5 h-5 text-gray-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path>
                      </svg>
                      <span className="text-gray-500 line-through">{feature}</span>
                    </li>
                  ))}
                </ul>
                <button
                  onClick={() => handleSelectPlan(tier.id)}
                  className={`w-full py-3 rounded-lg font-semibold transition-all duration-300 ${
                    userMembership && userMembership.membership_id === tier.id && userMembership.is_active
                      ? 'bg-gray-200 text-gray-600 cursor-not-allowed'
                      : tier.popular
                      ? 'bg-blue-600 text-white hover:bg-blue-700'
                      : 'bg-gray-100 text-gray-900 hover:bg-gray-200'
                  }`}
                  disabled={!!(userMembership && userMembership.membership_id === tier.id && userMembership.is_active)}
                >
                  {userMembership && userMembership.membership_id === tier.id && userMembership.is_active
                    ? '当前套餐'
                    : '选择套餐'
                  }
                </button>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-20 bg-white">
        <div className="max-w-6xl mx-auto px-6">
          <h2 className="text-3xl font-bold text-gray-900 mb-12 text-center">
            常见问题
          </h2>
          <div className="max-w-3xl mx-auto space-y-6">
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">如何选择适合我的会员套餐？</h3>
              <p className="text-gray-600">
                根据您的网站规模和优化需求选择。如果您是个人用户或小型网站，免费版可能足够。如果您需要更详细的分析和建议，建议选择基础会员或高级会员。大型企业和机构则适合企业会员。
              </p>
            </div>
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">会员套餐可以随时升级或降级吗？</h3>
              <p className="text-gray-600">
                是的，您可以随时升级或降级您的会员套餐。升级将立即生效，降级将在当前 billing 周期结束后生效。
              </p>
            </div>
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">检测次数是如何计算的？</h3>
              <p className="text-gray-600">
                每次提交检测请求就算一次检测次数。不同会员套餐有不同的月度检测次数限制。企业会员享受无限次检测。
              </p>
            </div>
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">技术支持的响应时间是多久？</h3>
              <p className="text-gray-600">
                基础会员的技术支持响应时间为1-2个工作日，高级会员为24小时内，企业会员享受24/7优先技术支持。
              </p>
            </div>
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">会员到期后会自动续费吗？</h3>
              <p className="text-gray-600">
                是的，会员到期后会自动续费。如果您不想继续使用会员服务，可以在会员中心取消自动续费。
              </p>
            </div>
            <div className="border border-gray-200 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">如何查看我的会员使用情况？</h3>
              <p className="text-gray-600">
                登录后，您可以在会员中心查看您的会员使用情况，包括剩余检测次数、会员到期时间等信息。
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
