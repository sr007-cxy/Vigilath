import { SalesAgentEmailRecords } from '../SalesAgent/SalesAgentEmailRecords';
import { resolveRole, useAuth } from '../../contexts/AuthContext';
import { PageHeader } from '../../components/ui';

export function SalesAgentEmailRecordsPage() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  return (
    <div className="space-y-4">
      <PageHeader title="邮件记录" hint="Sales Agent 发出的邮件触达与回执" />
      <SalesAgentEmailRecords admin={role === 'admin' || role === 'operator'} />
    </div>
  );
}
