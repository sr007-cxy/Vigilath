import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { agencyApi, type AgencyEnterprise, type AgencyMe } from '../services/agencyApi';

interface AgencyContextValue {
  loading: boolean;
  error: string | null;
  me: AgencyMe | null;
  enterprises: AgencyEnterprise[];
  enterpriseUserId: number | null;
  selectedEnterprise: AgencyEnterprise | null;
  managedAgency: { id: number; name: string; status: string } | null;
  canWrite: boolean;
  selectEnterprise: (userId: number | null) => void;
  reload: () => Promise<void>;
}

const AgencyContext = createContext<AgencyContextValue | null>(null);

export function AgencyProvider({ children }: { children: ReactNode }) {
  const { token, isAgency, role } = useAuth();
  const [params, setParams] = useSearchParams();
  const [loading, setLoading] = useState(isAgency);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<AgencyMe | null>(null);
  const [enterprises, setEnterprises] = useState<AgencyEnterprise[]>([]);
  const [managedAgency, setManagedAgency] = useState<{ id: number; name: string; status: string } | null>(null);

  const reload = useCallback(async () => {
    if (!token) {
      setMe(null); setEnterprises([]); setManagedAgency(null); setLoading(false); setError(null);
      return;
    }
    setLoading(true);
    try {
      if (isAgency) {
        const [identity, rows] = await Promise.all([agencyApi.me(token), agencyApi.enterprises(token)]);
        setMe(identity); setEnterprises(rows); setManagedAgency(null);
      } else if (role === 'user') {
        const relation = await agencyApi.accountAgency(token);
        setManagedAgency(relation.agency); setMe(null); setEnterprises([]);
      } else {
        setMe(null); setEnterprises([]); setManagedAgency(null);
      }
      setError(null);
    } catch (e) {
      setMe(null); setEnterprises([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [isAgency, role, token]);

  useEffect(() => { void reload(); }, [reload]);

  const requested = Number(params.get('enterpriseUserId') || 0) || null;
  const enterpriseUserId = requested && enterprises.some(row => row.enterprise.id === requested)
    ? requested : enterprises[0]?.enterprise.id ?? null;
  const selectedEnterprise = enterprises.find(row => row.enterprise.id === enterpriseUserId) || null;
  const canWrite = role === 'user' ? managedAgency === null
    : isAgency ? enterpriseUserId !== null : true;
  const selectEnterprise = useCallback((userId: number | null) => {
    const next = new URLSearchParams(params);
    if (userId) next.set('enterpriseUserId', String(userId));
    else next.delete('enterpriseUserId');
    setParams(next, { replace: true });
  }, [params, setParams]);

  const value = useMemo(() => ({
    loading, error, me, enterprises, enterpriseUserId, selectedEnterprise, managedAgency, canWrite,
    selectEnterprise, reload,
  }), [loading, error, me, enterprises, enterpriseUserId, selectedEnterprise, managedAgency, canWrite, selectEnterprise, reload]);
  return <AgencyContext.Provider value={value}>{children}</AgencyContext.Provider>;
}

// Keeping the Provider and hook in the same file is this module's public contract; the HMR rule only affects dev hot reloading.
// eslint-disable-next-line react-refresh/only-export-components
export function useAgencyContext() {
  const value = useContext(AgencyContext);
  if (!value) throw new Error('useAgencyContext requires AgencyProvider');
  return value;
}
