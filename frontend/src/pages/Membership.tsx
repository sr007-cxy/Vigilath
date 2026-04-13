import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { membershipApi, type Membership as MembershipType, type UsageResponse } from '../services/membershipApi';
import { authApi } from '../services/authApi';

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
  const navigate = useNavigate();
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [userMembership, setUserMembership] = useState<UserMembership | null>(null);
  const [membershipDetails, setMembershipDetails] = useState<MembershipType | null>(null);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      navigate('/login', { state: { from: '/membership' } });
      return;
    }

    let cancelled = false;
    const load = async () => {
      try {
        const user = await authApi.getCurrentUser(token);
        if (cancelled) return;
        setCurrentUser(user);

        // In parallel: user-membership, memberships list (for name lookup), usage
        const [um, allMemberships, usageData] = await Promise.allSettled([
          membershipApi.getUserMembership(token),
          membershipApi.getMemberships(),
          membershipApi.getUsage(token),
        ]);

        if (cancelled) return;

        const membership = um.status === 'fulfilled' ? um.value : null;
        setUserMembership(membership);

        if (membership && allMemberships.status === 'fulfilled') {
          const detail = allMemberships.value.find((m) => m.id === membership.membership_id) ?? null;
          setMembershipDetails(detail);
        }

        if (usageData.status === 'fulfilled') {
          setUsage(usageData.value);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load membership');
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const handleCancel = async () => {
    const token = localStorage.getItem('token');
    if (!token) {
      navigate('/login');
      return;
    }
    if (!window.confirm('确定要取消会员吗？')) return;
    try {
      await membershipApi.cancelMembership(token);
      alert('会员已取消');
      window.location.reload();
    } catch (err) {
      alert('取消会员失败：' + (err instanceof Error ? err.message : 'Unknown error'));
    }
  };

  const handleRenew = async () => {
    alert('支付功能即将上线，请稍后再试或联系客服。');
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

  const hasActiveMembership = !!(userMembership && userMembership.is_active);

  return (
    <div className="bg-white min-h-screen">
      <section className="relative overflow-hidden bg-gradient-to-r from-blue-600 to-blue-800 text-white py-16 md:py-20">
        <div className="max-w-5xl mx-auto px-6 relative z-10">
          <h1 className="text-3xl md:text-4xl font-bold mb-3">会员中心</h1>
          <p className="text-lg text-blue-100">
            {currentUser ? `欢迎，${currentUser.name}` : ''}
          </p>
        </div>
      </section>

      <main className="py-12 px-6">
        <div className="max-w-5xl mx-auto space-y-6">
          {/* Current plan card */}
          <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
            <h2 className="text-xl font-bold text-gray-900 mb-4">当前套餐</h2>
            {hasActiveMembership && membershipDetails ? (
              <div className="space-y-3">
                <div className="flex items-baseline gap-3">
                  <span className="text-2xl font-bold text-gray-900">{membershipDetails.name}</span>
                  <span className="text-sm text-gray-500">
                    {membershipDetails.tier_type === 'saas'
                      ? `$${membershipDetails.price}${membershipDetails.period}`
                      : '人工服务'}
                  </span>
                </div>
                <p className="text-sm text-gray-600">{membershipDetails.description}</p>
                {userMembership && (
                  <p className="text-xs text-gray-500">
                    到期时间：{new Date(userMembership.end_date).toLocaleDateString()}
                  </p>
                )}
              </div>
            ) : (
              <div>
                <p className="text-gray-600 mb-4">您当前没有活跃的会员订阅（按免费会员计算）。</p>
                <button
                  onClick={() => navigate('/products-services')}
                  className="px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-semibold"
                >
                  查看套餐
                </button>
              </div>
            )}
          </div>

          {/* Usage card */}
          {usage && (
            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-xl font-bold text-gray-900 mb-4">本月检测用量</h2>
              <div className="flex items-baseline gap-2 mb-3">
                <span className="text-3xl font-bold text-gray-900">
                  {usage.used}
                </span>
                <span className="text-gray-500">
                  / {usage.quota === 0 ? '∞' : usage.quota} 次
                </span>
                <span className="text-xs text-gray-400 ml-auto">{usage.year_month}</span>
              </div>
              {usage.quota > 0 && (
                <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-500"
                    style={{
                      width: `${Math.min(100, Math.round((usage.used / usage.quota) * 100))}%`,
                    }}
                  ></div>
                </div>
              )}
              <p className="text-xs text-gray-500 mt-3">
                {usage.quota === 0
                  ? '您当前套餐无检测次数限制。'
                  : `剩余 ${usage.remaining} 次，月初自动重置。`}
              </p>
            </div>
          )}

          {/* Actions card */}
          <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
            <h2 className="text-xl font-bold text-gray-900 mb-4">账户操作</h2>
            <div className="flex flex-col sm:flex-row gap-3">
              <button
                onClick={() => navigate('/products-services')}
                className="flex-1 px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-semibold"
              >
                浏览套餐
              </button>
              {hasActiveMembership && (
                <>
                  <button
                    onClick={handleRenew}
                    className="flex-1 px-5 py-2.5 bg-gray-100 text-gray-900 rounded-lg hover:bg-gray-200 transition-colors font-semibold"
                  >
                    续费
                  </button>
                  <button
                    onClick={handleCancel}
                    className="flex-1 px-5 py-2.5 bg-white border border-rose-300 text-rose-600 rounded-lg hover:bg-rose-50 transition-colors font-semibold"
                  >
                    取消会员
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
