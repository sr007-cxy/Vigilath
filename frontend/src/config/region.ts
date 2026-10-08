/**
 * Site region -- after the ICP filing remediation this repo only builds the CN site; the global site is frozen on
 * the `release/vigilath-com` branch. This switch **no longer gates regional content**;
 * it only keeps its original deployment-side duties: aligning with the backend `settings.REGION`
 * (backend/geo/database.py), and reading the filing / license numbers below.
 *
 *   VITE_REGION=cn -> CN site (must be set in the test host's .env; the backend likewise sets REGION=cn)
 *
 * Don't gate new "overseas content" with `if (IS_CN)` anymore; just don't write it into the code.
 */
export type Region = 'global' | 'cn';

export const REGION: Region =
  String(import.meta.env.VITE_REGION || '').trim().toLowerCase() === 'cn' ? 'cn' : 'global';

export const IS_CN = REGION === 'cn';
export const IS_GLOBAL = !IS_CN;

/**
 * ICP filing number (required for the CN site, injected via .env, not in the codebase). Empty = not shown.
 *
 * The same build serves both vigilath.cn and vigilath.com.cn, which have different filing numbers
 * (-5 / -4), so it's chosen by runtime domain: when vigilath.com.cn matches, use
 * VITE_ICP_BEIAN_COM_CN, otherwise fall back to VITE_ICP_BEIAN.
 */
const ICP_BEIAN_DEFAULT: string = String(import.meta.env.VITE_ICP_BEIAN || '<icp-beian>').trim();
const ICP_BEIAN_COM_CN: string = String(import.meta.env.VITE_ICP_BEIAN_COM_CN || '').trim();

export const ICP_BEIAN: string =
  ICP_BEIAN_COM_CN &&
  typeof window !== 'undefined' &&
  /(^|\.)vigilath\.com\.cn$/i.test(window.location.hostname)
    ? ICP_BEIAN_COM_CN
    : ICP_BEIAN_DEFAULT;

/** Public security network filing number (optional). Empty = not shown. */
export const POLICE_BEIAN: string = String(import.meta.env.VITE_POLICE_BEIAN || '<police-beian>').trim();

/**
 * Value-added telecom business license number (EDI license, e.g. "<edi-license>").
 * The "Special Provisions" require the license number to be shown prominently on the website home page, so it's required for the CN site.
 * Empty = not shown.
 */
export const EDI_LICENSE: string = String(import.meta.env.VITE_EDI_LICENSE || '<edi-license>').trim();
