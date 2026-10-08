// Publishing plugin installation guide page. The publish center sends users here when it can't detect the plugin.
//
// Why this page is needed: regular users (non-staff) **can only see the plugin channel** in the publish center -- without the plugin
// they can't use publishing at all. Previously the install URL here was the `https://example.com/wechatsync-plugin`
// placeholder, rendered directly to users in three places in the publish center, and clicking it gave a 404.
import { useEffect, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { PageHead } from '../components/PageHead';

// Distribution channels. **The offline install package is currently the only channel that actually installs** -- it's not on the Chrome Web Store yet,
// so the store channel keeps a slot but isn't put first, lest users follow steps to search for an extension they can't find.
// Drag-and-drop .crx install has been blocked by Chrome on Windows/Mac since v75, only possible via enterprise policy, so it's not shown to regular users.
type Channel = 'zip' | 'edge' | 'chrome';

// Use /downloads/ rather than /plugin/ -- the latter collides with this page's own route and returns 403, see pack-web.mjs
const ZIP_PATH = '/downloads/plugin/vigilath-publish-plugin-latest.zip';

const CHANNELS: { id: Channel; name: string; badge?: string; soon?: boolean; hint: string }[] = [
  { id: 'zip', name: '离线安装包', badge: '当前推荐', hint: '不依赖任何商店，Chrome 和 Edge 都能装，但需要手动更新' },
  { id: 'edge', name: 'Edge 加载项', hint: '装完自动更新，国内可直接访问' },
  { id: 'chrome', name: 'Chrome 应用店', soon: true, hint: '暂未上架，请先用离线安装包' },
];

const card: CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
  borderRadius: 12, padding: 20,
};

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 14 }}>
      <span style={{
        flex: '0 0 24px', width: 24, height: 24, borderRadius: '50%', background: '#2563eb',
        color: '#fff', fontSize: 13, fontWeight: 700, display: 'inline-flex',
        alignItems: 'center', justifyContent: 'center',
      }}>{n}</span>
      <span style={{ lineHeight: 1.7 }}>{children}</span>
    </li>
  );
}

/** Don't make users guess whether installation succeeded -- probe window.$syncer directly */
function InstallCheck() {
  const [state, setState] = useState<'checking' | 'ok' | 'missing'>('checking');

  useEffect(() => {
    let done = false;
    const deadline = Date.now() + 2500;
    const timer = window.setInterval(() => {
      const w = window as unknown as { $syncer?: unknown; $poster?: unknown };
      if (w.$syncer || w.$poster) {
        done = true; window.clearInterval(timer); setState('ok');
      } else if (Date.now() >= deadline) {
        done = true; window.clearInterval(timer); setState('missing');
      }
    }, 100);
    return () => { if (!done) window.clearInterval(timer); };
  }, []);

  if (state === 'checking') {
    return <p style={{ color: 'var(--text-secondary)' }}>正在检测插件…</p>;
  }
  if (state === 'ok') {
    return (
      <div style={{ ...card, borderColor: '#16a34a', background: 'rgba(22,163,74,.08)' }}>
        <strong style={{ color: '#16a34a' }}>✓ 插件已安装并启用</strong>
        <p style={{ margin: '8px 0 0', lineHeight: 1.7 }}>
          下一步：在这个浏览器里登录你要发布的平台（知乎、头条号等），
          然后回到 <Link to="/app/content?tab=library">内容库</Link> 选稿件发布。
        </p>
      </div>
    );
  }
  return (
    <div style={{ ...card, borderColor: '#d97706', background: 'rgba(217,119,6,.08)' }}>
      <strong style={{ color: '#d97706' }}>未检测到插件</strong>
      <p style={{ margin: '8px 0 0', lineHeight: 1.7 }}>
        如果你刚装好，请<strong>刷新本页</strong>再看 —— 扩展需要重新注入到页面里。
        仍然检测不到的话，到浏览器的扩展管理页确认它处于「已启用」状态。
      </p>
    </div>
  );
}

export default function PluginInstall() {
  const [channel, setChannel] = useState<Channel>('zip');

  return (
    <>
      <PageHead
        titleKey="pluginInstall.title"
        titleFallback="安装发布插件 · Vigilath"
        descriptionKey="pluginInstall.desc"
        descriptionFallback="安装 Vigilath 发布插件，用浏览器里已登录的账号把稿件一键发布到知乎、头条号、公众号等平台。"
      />
      <div style={{ maxWidth: 780, margin: '0 auto', padding: '40px 20px 80px' }}>
        <h1 style={{ fontSize: 30, marginBottom: 10 }}>安装发布插件</h1>
        <p style={{ color: 'var(--text-secondary)', lineHeight: 1.8, marginBottom: 28 }}>
          插件让你用<strong>自己浏览器里已经登录的账号</strong>把稿件一键发到各个平台。
          平台的登录状态<strong>只留在你本机</strong>，不会上传。
        </p>

        <div style={{ ...card, marginBottom: 28 }}>
          <h2 style={{ fontSize: 17, marginTop: 0, marginBottom: 14 }}>安装状态</h2>
          <InstallCheck />
        </div>

        <h2 style={{ fontSize: 20, marginBottom: 12 }}>选择安装方式</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
          {CHANNELS.map(c => (
            <button
              key={c.id}
              onClick={() => setChannel(c.id)}
              style={{
                padding: '10px 16px', borderRadius: 8, cursor: 'pointer', fontSize: 14,
                fontWeight: channel === c.id ? 700 : 500,
                border: `1px solid ${channel === c.id ? '#2563eb' : 'var(--border-color)'}`,
                background: channel === c.id ? 'rgba(37,99,235,.1)' : 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
              }}
            >
              {c.name}
              {c.badge && (
                <span style={{
                  marginLeft: 8, fontSize: 11, padding: '2px 6px', borderRadius: 4,
                  background: '#16a34a', color: '#fff', fontWeight: 700,
                }}>{c.badge}</span>
              )}
              {c.soon && (
                <span style={{
                  marginLeft: 8, fontSize: 11, padding: '2px 6px', borderRadius: 4,
                  background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)',
                  color: 'var(--text-secondary)', fontWeight: 700,
                }}>暂未上架</span>
              )}
            </button>
          ))}
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 18 }}>
          {CHANNELS.find(c => c.id === channel)!.hint}
        </p>

        <div style={card}>
          {channel === 'edge' && (
            <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              <Step n={1}>打开 Edge 浏览器，进入 Edge 加载项商店搜索「Vigilath 发布助手」。</Step>
              <Step n={2}>点「获取」→「添加扩展」。</Step>
              <Step n={3}>装好后<strong>刷新本页</strong>，上面的安装状态会变成绿色。</Step>
              <Step n={4}>在同一个浏览器里登录你要发布的平台（知乎、头条号等）。</Step>
            </ol>
          )}

          {channel === 'chrome' && (
            <div style={{ lineHeight: 1.8 }}>
              <strong>Chrome 网上应用店还在上架流程里</strong>
              <p style={{ margin: '8px 0 0' }}>
                现在到应用店里搜是搜不到的。Chrome 用户请改用
                <button
                  onClick={() => setChannel('zip')}
                  style={{
                    background: 'none', border: 'none', padding: '0 4px', cursor: 'pointer',
                    color: 'var(--accent-primary)', font: 'inherit', textDecoration: 'underline',
                  }}
                >离线安装包</button>
                —— 装出来的是同一个扩展，功能没有差别，只是不会自动更新
                （有新版时插件会在弹窗里提示你）。
              </p>
            </div>
          )}

          {channel === 'zip' && (
            <>
              <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                <Step n={1}>
                  下载安装包：
                  <a href={ZIP_PATH} download style={{ color: 'var(--accent-primary)', marginLeft: 6 }}>
                    vigilath-publish-plugin.zip
                  </a>
                </Step>
                <Step n={2}>解压到一个<strong>固定目录</strong>（之后不要移动或删除，浏览器每次启动都要读它）。</Step>
                <Step n={3}>
                  地址栏输入 <code>chrome://extensions</code>（Edge 是 <code>edge://extensions</code>），
                  打开右上角的<strong>开发者模式</strong>。
                </Step>
                <Step n={4}>点「加载已解压的扩展程序」，选中第 2 步解压出来的文件夹。</Step>
                <Step n={5}>回到本页<strong>刷新</strong>确认状态变绿。</Step>
              </ol>
              <p style={{
                marginTop: 16, marginBottom: 0, padding: 12, borderRadius: 8,
                background: 'rgba(217,119,6,.08)', fontSize: 13, lineHeight: 1.7,
              }}>
                离线安装包<strong>不会自动更新</strong>，有新版本时插件会在弹窗里提示你。
                另外部分浏览器每次启动会提示「请停用以开发者模式运行的扩展程序」，
                点「取消」即可，不要点停用。
              </p>
            </>
          )}
        </div>

        <h2 style={{ fontSize: 20, margin: '32px 0 12px' }}>常见问题</h2>
        <div style={{ ...card, lineHeight: 1.8 }}>
          <p style={{ marginTop: 0 }}>
            <strong>装了插件，发布中心还是说没装？</strong><br />
            刷新页面。扩展需要重新注入到已经打开的页面里，装完之前打开的标签页读不到它。
          </p>
          <p>
            <strong>发布中心里看不到我的平台账号？</strong><br />
            插件只读你<strong>当前浏览器里已登录</strong>的账号，它不会替你登录。
            先在同一个浏览器里打开目标平台正常登录一次，再回发布中心刷新。
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>插件会拿到我的账号密码吗？</strong><br />
            不会。它读的是浏览器已有的登录状态，不接触密码。
            这份登录状态默认只留在你本机；只有你在账号中心主动点「导入账号池」时，
            才会加密上传到服务器用于无人值守发布。
          </p>
        </div>
      </div>
    </>
  );
}
