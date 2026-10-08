// Temporary component preview page (not in build output; only for local visual acceptance of the UI spec)
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import '../src/index.css';
import { Button, Badge, StatusDot, Card, PageHeader, Input, Textarea, Field, PillTabs } from '../src/components/ui';
import { TableShell, StickyHead, SkeletonRows, EmptyState } from '../src/components/ui/DataTable';

function Demo() {
  const [tab, setTab] = useState('all');
  const [theme, setTheme] = useState('peec');
  const apply = (t: string) => { setTheme(t); document.body.setAttribute('data-theme', t); };
  return (
    <div style={{ padding: 24, minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <PageHeader title="UI 规范预览" hint="Button / Badge / Card / Field / PillTabs / 表格态"
        actions={<PillTabs value={theme} onChange={apply}
          items={[{ value: 'peec', label: 'peec' }, { value: 'light', label: 'light' }, { value: 'dark', label: 'dark' }]} />} />

      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <Button variant="solid">主操作</Button>
          <Button>次要操作</Button>
          <Button variant="ghost">幽灵</Button>
          <Button variant="danger">删除</Button>
          <Button loading>保存中</Button>
          <Button disabled>已禁用</Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="solid">小</Button>
          <Button size="md" variant="solid">中</Button>
          <Button size="lg" variant="solid">大</Button>
        </div>
      </Card>

      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <Badge>中性</Badge><Badge tone="success">正常</Badge><Badge tone="warn">待处理</Badge>
          <Badge tone="danger">已停用</Badge><Badge tone="info">进行中</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <StatusDot tone="success">运行中</StatusDot>
          <StatusDot tone="warn">排队</StatusDot>
          <StatusDot tone="danger">失败</StatusDot>
        </div>
      </Card>

      <Card className="mb-4">
        <PillTabs value={tab} onChange={setTab} className="mb-3"
          items={[{ value: 'all', label: '全部', count: 42 }, { value: 'on', label: '正常', count: 39 },
                  { value: 'off', label: '已停用', count: 3 }]} />
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
          <Field label="邮箱" required hint="用于登录"><Input placeholder="user@example.com" /></Field>
          <Field label="名称"><Input size="sm" placeholder="选填" /></Field>
          <Field label="备注" error="这里填错了"><Input invalid placeholder="出错示例" /></Field>
        </div>
        <div className="mt-3"><Field label="描述"><Textarea rows={2} placeholder="多行输入" /></Field></div>
      </Card>

      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(2, minmax(0,1fr))' }}>
        <TableShell maxHeight="200px">
          <table className="w-full text-xs">
            <StickyHead>
              <th className="text-left px-3 py-2">列一</th>
              <th className="text-left px-3 py-2">列二</th>
              <th className="text-left px-3 py-2">列三</th>
            </StickyHead>
            <SkeletonRows rows={5} cols={3} />
          </table>
        </TableShell>
        <Card padded={false}>
          <EmptyState title="没有匹配的记录" hint="换个关键词,或把筛选放回「全部」再看。"
            action={<Button size="sm">清空筛选条件</Button>} />
        </Card>
      </div>
    </div>
  );
}
createRoot(document.getElementById('root')!).render(<Demo />);
