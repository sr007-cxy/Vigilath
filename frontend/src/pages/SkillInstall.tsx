// Install page -- opening /skill in a browser 302s here (an agent fetching /skill gets plain-text instructions).
// This page covers only one thing: how to install Vigilath into your AI agent.
import { useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { PageHead } from '../components/PageHead';
import { useAuthModal } from '../contexts/AuthModalContext';

// The default install is the 8 narrow skills, **not** the all-in-one vigilath-geo package -- every example path on the page must
// use a directory that actually gets installed, otherwise a "copy and run" command pasted into the terminal gives No such file or directory.
const SKILL_DIR = '~/.claude/skills/vigilath-site-audit';
const VERIFY_CMD = `python3 ${SKILL_DIR}/scripts/geo_client.py capabilities`;
const ANON_CMDS = [
  { cmd: `python3 ${SKILL_DIR}/scripts/geo_client.py check https://example.com`,
    what: '看一个网站在 AI 搜索里的可见性得分与问题清单' },
  { cmd: `python3 ${SKILL_DIR}/scripts/geo_client.py mention 你的品牌 你的行业词`,
    what: '看各家 AI 被问到这个行业时,会不会提到你' },
  { cmd: `python3 ${SKILL_DIR}/scripts/geo_client.py industry`,
    what: '看已经收录的行业库' },
];

const AGENTS = [
  { name: 'OpenClaw', dir: '~/.openclaw/skills' },
  { name: 'Claude Code', dir: '~/.claude/skills' },
  { name: '通用 agent 目录', dir: '~/.agents/skills' },
  { name: '当前项目', dir: './skills' },
];

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={() => { void navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}
      style={{
        padding: '8px 16px', borderRadius: 8, border: 'none', cursor: 'pointer',
        background: '#2563eb', color: '#fff', fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap',
      }}
    >
      {done ? '已复制 ✓' : '复制'}
    </button>
  );
}

export default function SkillInstall() {
  const { openAuthModal } = useAuthModal();
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://vigilath.cn';
  const cmd = `curl -fsSL ${origin}/skill/install.sh | bash`;

  const wrap: CSSProperties = {
    maxWidth: 760, margin: '0 auto', padding: '56px 24px 80px',
    fontFamily: 'system-ui, -apple-system, sans-serif', color: '#1f2937', lineHeight: 1.75,
  };
  const codeBox: CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 12, marginTop: 14,
    padding: '14px 16px', borderRadius: 12, background: '#0f172a',
  };
  const codeText: CSSProperties = {
    flex: 1, color: '#e2e8f0', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontSize: 13, wordBreak: 'break-all',
  };
  const step: CSSProperties = { marginTop: 34 };
  const stepNo: CSSProperties = {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26,
    borderRadius: '50%', background: '#2563eb', color: '#fff', fontSize: 14, fontWeight: 700, marginRight: 10,
  };
  const note: CSSProperties = { fontSize: 14, color: '#6b7280', marginTop: 10 };
  const linkBtn: CSSProperties = {
    background: 'none', border: 'none', padding: 0, cursor: 'pointer',
    color: '#2563eb', font: 'inherit',
  };

  return (
    <>
      <PageHead
        titleKey="skillInstall.title"
        titleFallback="把 Vigilath 装进你的 AI 助手"
        descriptionKey="skillInstall.desc"
        descriptionFallback="一条命令,让你的 AI 助手能查品牌在 AI 搜索引擎中的可见性、被引用率与舆情。"
      />
      <div style={wrap}>
        <h1 style={{ fontSize: 32, fontWeight: 700, letterSpacing: -0.5 }}>把 Vigilath 装进你的 AI 助手</h1>
        <p style={{ fontSize: 17, color: '#4b5563', marginTop: 12 }}>
          装完之后,直接问它「我的品牌在 AI 里被搜到几个问题」「今天有没有负面」「竞品在 AI 引擎里怎么比」,
          它会连到你账号的数据回答你。
        </p>

        <div style={step}>
          <h2 style={{ fontSize: 19, fontWeight: 600 }}><span style={stepNo}>1</span>在装 agent 的那台机器上跑这行</h2>
          <div style={codeBox}>
            <code style={codeText}>{cmd}</code>
            <Copy text={cmd} />
          </div>
          <p style={note}>也可以把本页地址 <code>{origin}/skill</code> 直接发给你的 AI 助手,让它自己照着装。</p>
        </div>

        <div style={step}>
          <h2 style={{ fontSize: 19, fontWeight: 600 }}><span style={stepNo}>2</span>装完直接试,不需要账号</h2>
          <p style={note}>这三件事免登录、免费,装好就能用:</p>
          {ANON_CMDS.map((c) => (
            <div key={c.cmd} style={{ marginTop: 12 }}>
              <div style={codeBox}>
                <code style={codeText}>{c.cmd}</code>
                <Copy text={c.cmd} />
              </div>
              <p style={{ ...note, marginTop: 6 }}>{c.what}</p>
            </div>
          ))}
          <p style={note}>
            路径以安装脚本最后一行打印的为准 —— 它会把实际装到的目录告诉你。
          </p>
        </div>

        <div style={step}>
          <h2 style={{ fontSize: 19, fontWeight: 600 }}>
            <span style={{ ...stepNo, background: '#6b7280' }}>3</span>
            可选:连上你的账号
          </h2>
          <p style={note}>
            报告中心、持续监测、产稿、舆情这几个技能要连账号才能用。跑一行
            <code> geo_client.py login</code>,终端会出一个 8 位配对码(形如 <strong style={{ color: '#1f2937' }}>JS2U-VRGK</strong>,
            10 分钟内有效、只能用一次),到 <Link to="/link" style={{ color: '#2563eb' }}>{origin}/link</Link> 输入并点「批准」,
            终端自动继续 —— 全程不用复制粘贴任何密钥。
          </p>
          <p style={note}>
            还没有账号?<button onClick={() => openAuthModal('register')} style={linkBtn}>微信扫码注册</button>,一分钟。
          </p>
        </div>

        <div style={{ ...step, padding: '20px 22px', background: '#f9fafb', borderRadius: 12 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600 }}>会装到哪里</h3>
          <p style={note}>脚本会装进机器上所有已存在的 skills 目录:</p>
          <ul style={{ marginTop: 8, paddingLeft: 20, fontSize: 14, color: '#4b5563' }}>
            {AGENTS.map((a) => (
              <li key={a.dir} style={{ marginTop: 4 }}>
                {a.name} —— <code style={{ fontSize: 13 }}>{a.dir}/vigilath-site-audit</code> 等 8 个
              </li>
            ))}
          </ul>
          <p style={note}>
            都不存在时装到 <code>~/.vigilath/skills</code>;想只装一处,命令后面加 <code>-s -- --dir &lt;路径&gt;</code>。
            凭证写在 <code>~/.vigilath/config</code>(权限 600),不会进命令历史。
          </p>
        </div>

        <div style={step}>
          <h3 style={{ fontSize: 16, fontWeight: 600 }}>连上账号之后验证</h3>
          <div style={codeBox}>
            <code style={codeText}>{VERIFY_CMD}</code>
            <Copy text={VERIFY_CMD} />
          </div>
          <p style={note}>
            凭证过期或要换账号:跑同目录下的 <code>geo_client.py login</code> 重新授权一次,
            <strong style={{ color: '#1f2937' }}>不必重装</strong> —— 重跑安装命令会覆盖已经装好的技能目录。
            无人值守的机器可以改用带 token 的装法,在 <Link to="/account/integration" style={{ color: '#2563eb' }}>账号 · 对接集成</Link> 页生成。
          </p>
        </div>
      </div>
    </>
  );
}
