// SMS channel icons -- shared by the channel picker, detection hint bar and channel list.
//
// Isomorphic with [[EmailProviderIcon]]: uniformly sized rounded color tiles; path kinds use official monochrome logo outlines
// (from simple-icons, graphics CC0; trademarks belong to their owners, used here only to identify the provider),
// text kinds use brand-color background + white abbreviation. Tencent Cloud has no public monochrome logo, so it uses text.
//
// Kept separate rather than merged into EmailProviderIcon: the two key spaces don't overlap (preset.key),
// and with one merged table, adding a platform on either side would require checking the other side's comments for key collisions.

import type { CSSProperties } from 'react';

const ICON_PATHS = {
  twilio: 'M12 0C5.381-.008.008 5.352 0 11.971V12c0 6.64 5.359 12 12 12 6.64 0 12-5.36 12-12 0-6.641-5.36-12-12-12zm0 20.801c-4.846.015-8.786-3.904-8.801-8.75V12c-.014-4.846 3.904-8.786 8.75-8.801H12c4.847-.014 8.786 3.904 8.801 8.75V12c.015 4.847-3.904 8.786-8.75 8.801H12zm5.44-11.76c0 1.359-1.12 2.479-2.481 2.479-1.366-.007-2.472-1.113-2.479-2.479 0-1.361 1.12-2.481 2.479-2.481 1.361 0 2.481 1.12 2.481 2.481zm0 5.919c0 1.36-1.12 2.48-2.481 2.48-1.367-.008-2.473-1.114-2.479-2.48 0-1.359 1.12-2.479 2.479-2.479 1.361-.001 2.481 1.12 2.481 2.479zm-5.919 0c0 1.36-1.12 2.48-2.479 2.48-1.368-.007-2.475-1.113-2.481-2.48 0-1.359 1.12-2.479 2.481-2.479 1.358-.001 2.479 1.12 2.479 2.479zm0-5.919c0 1.359-1.12 2.479-2.479 2.479-1.367-.007-2.475-1.112-2.481-2.479 0-1.361 1.12-2.481 2.481-2.481 1.358 0 2.479 1.12 2.479 2.481z',
  alibabaCloud: 'M3.996 4.517h5.291L8.01 6.324 4.153 7.506a1.668 1.668 0 0 0-1.165 1.601v5.786a1.668 1.668 0 0 0 1.165 1.6l3.857 1.183 1.277 1.807H3.996A3.996 3.996 0 0 1 0 15.487V8.513a3.996 3.996 0 0 1 3.996-3.996m16.008 0h-5.291l1.277 1.807 3.857 1.182c.715.227 1.17.889 1.165 1.601v5.786a1.668 1.668 0 0 1-1.165 1.6l-3.857 1.183-1.277 1.807h5.291A3.996 3.996 0 0 0 24 15.487V8.513a3.996 3.996 0 0 0-3.996-3.996m-4.007 8.345H8.002v-1.804h7.995Z',
  aws: 'M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.063.056.127.056.183 0 .08-.048.16-.152.24l-.503.335a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.239-.112a2.47 2.47 0 0 1-.287-.375 6.18 6.18 0 0 1-.248-.471c-.622.734-1.405 1.101-2.347 1.101-.67 0-1.205-.191-1.596-.574-.391-.384-.59-.894-.59-1.533 0-.678.239-1.23.726-1.644.487-.415 1.133-.623 1.955-.623.272 0 .551.024.846.064.296.04.6.104.918.176v-.583c0-.607-.127-1.03-.375-1.277-.255-.248-.686-.367-1.3-.367-.28 0-.568.031-.863.103-.295.072-.583.16-.862.272a2.287 2.287 0 0 1-.28.104.488.488 0 0 1-.127.023c-.112 0-.168-.08-.168-.247v-.391c0-.128.016-.224.056-.28a.597.597 0 0 1 .224-.167c.279-.144.614-.264 1.005-.36a4.84 4.84 0 0 1 1.246-.151c.95 0 1.644.216 2.091.647.439.43.662 1.085.662 1.963v2.586zm-3.24 1.214c.263 0 .534-.048.822-.144.287-.096.543-.271.758-.51.128-.152.224-.32.272-.512.047-.191.08-.423.08-.694v-.335a6.66 6.66 0 0 0-.735-.136 6.02 6.02 0 0 0-.75-.048c-.535 0-.926.104-1.19.32-.263.215-.39.518-.39.917 0 .375.095.655.295.846.191.2.47.296.838.296zm6.41.862c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.311L7.586 5.55a1.398 1.398 0 0 1-.072-.32c0-.128.064-.2.191-.2h.783c.151 0 .255.025.31.08.065.048.113.16.16.312l1.342 5.284 1.245-5.284c.04-.16.088-.264.151-.312a.549.549 0 0 1 .32-.08h.638c.152 0 .256.025.32.08.063.048.12.16.151.312l1.261 5.348 1.381-5.348c.048-.16.104-.264.16-.312a.52.52 0 0 1 .311-.08h.743c.127 0 .2.065.2.2 0 .04-.009.08-.017.128a1.137 1.137 0 0 1-.056.2l-1.923 6.17c-.048.16-.104.263-.168.311a.51.51 0 0 1-.303.08h-.687c-.151 0-.255-.024-.32-.08-.063-.056-.119-.16-.15-.32l-1.238-5.148-1.23 5.14c-.04.16-.087.264-.15.32-.065.056-.177.08-.32.08zm10.256.215c-.415 0-.83-.048-1.229-.143-.399-.096-.71-.2-.918-.32-.128-.071-.215-.151-.247-.223a.563.563 0 0 1-.048-.224v-.407c0-.167.064-.247.183-.247.048 0 .096.008.144.024.048.016.12.048.2.08.271.12.566.215.878.279.319.064.63.096.95.096.502 0 .894-.088 1.165-.264a.86.86 0 0 0 .415-.758.777.777 0 0 0-.215-.559c-.144-.151-.416-.287-.807-.415l-1.157-.36c-.583-.183-1.014-.454-1.277-.813a1.902 1.902 0 0 1-.4-1.158c0-.335.073-.63.216-.886.144-.255.335-.479.575-.654.24-.184.51-.32.83-.415.32-.096.655-.136 1.006-.136.175 0 .359.008.535.032.183.024.35.056.518.088.16.04.312.08.455.127.144.048.256.096.336.144a.69.69 0 0 1 .24.2.43.43 0 0 1 .071.263v.375c0 .168-.064.256-.184.256a.83.83 0 0 1-.303-.096 3.652 3.652 0 0 0-1.532-.311c-.455 0-.815.071-1.062.223-.248.152-.375.383-.375.71 0 .224.08.416.24.567.159.152.454.304.877.44l1.134.358c.574.184.99.44 1.237.767.247.327.367.702.367 1.117 0 .343-.072.655-.207.926-.144.272-.336.511-.583.703-.248.2-.543.343-.886.447-.36.111-.734.167-1.142.167zM21.698 16.207c-2.626 1.94-6.442 2.969-9.722 2.969-4.598 0-8.74-1.7-11.87-4.526-.247-.223-.024-.527.272-.351 3.384 1.963 7.559 3.153 11.877 3.153 2.914 0 6.114-.607 9.06-1.852.439-.2.814.287.383.607zM22.792 14.961c-.336-.43-2.22-.207-3.074-.103-.255.032-.295-.192-.063-.36 1.5-1.053 3.967-.75 4.254-.399.287.36-.08 2.826-1.485 4.007-.215.184-.423.088-.327-.151.32-.79 1.03-2.57.695-2.994z',
  // Custom HTTP gateway / fallback -- a self-drawn speech bubble. Both subpaths wind the same way,
  // otherwise nonzero fill would punch a hole where the bubble and tail overlap.
  bubble:
    'M3.25 6.6A2.35 2.35 0 0 1 5.6 4.25h12.8A2.35 2.35 0 0 1 20.75 6.6v6.8A2.35 2.35 0 0 1 18.4 15.75H9.4L5.25 19.4A.75.75 0 0 1 4 18.84V15.6A2.35 2.35 0 0 1 3.25 13.4Z M7.4 8.6h9.2v1.6H7.4Z',
} as const;

type BrandMark = { color: string; path?: string; text?: string };

/** key = the backend preset's key (sms_config.py: SALES_AGENT_SMS_PROVIDER_PRESETS). */
const PROVIDER_BRANDS: Record<string, BrandMark> = {
  tencent_cloud: { color: '#006EFF', text: '腾' },
  aliyun: { color: '#FF6A00', path: ICON_PATHS.alibabaCloud },
  twilio: { color: '#F22F46', path: ICON_PATHS.twilio },
  // AWS brand colors are dark blue + orange; on a single-color tile dark blue is closer to the official dark background look
  aws_sns: { color: '#232F3E', path: ICON_PATHS.aws },
  custom_http: { color: '#64748B', path: ICON_PATHS.bubble },
};

/** The more characters, the smaller the font; single Chinese characters need one size up over Latin letters to hold the tile. */
const TEXT_SCALE: Record<number, number> = { 1: 0.54, 2: 0.4, 3: 0.34, 4: 0.27 };

export function SmsProviderIcon({
  iconKey,
  size = 26,
  style,
}: {
  iconKey: string;
  size?: number;
  style?: CSSProperties;
}) {
  const brand = PROVIDER_BRANDS[iconKey] || PROVIDER_BRANDS.custom_http;
  return (
    <span
      aria-hidden
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: Math.round(size * 0.28),
        background: brand.color,
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: 'inset 0 0 0 1px rgba(255, 255, 255, 0.18)',
        ...style,
      }}
    >
      {brand.path ? (
        <svg viewBox="0 0 24 24" width={size * 0.6} height={size * 0.6} fill="currentColor" focusable="false">
          <path d={brand.path} />
        </svg>
      ) : (
        <span
          style={{
            fontSize: Math.max(8, Math.round(size * (TEXT_SCALE[(brand.text || '').length] ?? 0.3))),
            fontWeight: 800,
            lineHeight: 1,
            letterSpacing: (brand.text || '').length > 2 ? -0.4 : 0,
          }}
        >
          {brand.text}
        </span>
      )}
    </span>
  );
}
