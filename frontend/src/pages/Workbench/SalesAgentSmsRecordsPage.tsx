import { SalesAgentSmsRecords } from '../SalesAgent/SalesAgentSmsRecords';
import { resolveRole, useAuth } from '../../contexts/AuthContext';
import { PageHeader } from '../../components/ui';

export function SalesAgentSmsRecordsPage() {
  const { user } = useAuth();
  const role = user ? resolveRole(user) : '';
  return (
    <div className="space-y-4">
      <PageHeader title="短信记录" hint="Sales Agent 发出的短信触达与回执" />
      <SalesAgentSmsRecords admin={role === 'admin' || role === 'operator'} />
    </div>
  );
}
