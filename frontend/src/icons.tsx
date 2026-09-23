// Small, hand-drawn SVG icon set — no emoji, no icon-font dependency.
// All stroke icons share the same viewBox/attrs so they sit consistently at any size.
import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Stroke({ children, ...p }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...p}
    >
      {children}
    </svg>
  );
}

export const IconSearch = (p: IconProps) => (
  <Stroke {...p}><circle cx="10" cy="10" r="6" /><line x1="14.6" y1="14.6" x2="20" y2="20" /></Stroke>
);

export const IconChat = (p: IconProps) => (
  <Stroke {...p}><rect x="3" y="4.5" width="18" height="12" rx="2.5" /><path d="M8 16.5 6 20l5-3.5" /></Stroke>
);

export const IconCheck = (p: IconProps) => (
  <Stroke {...p}><rect x="4" y="4" width="16" height="16" rx="4" /><polyline points="8,12.5 11,15.5 16,9" /></Stroke>
);

export const IconBars = (p: IconProps) => (
  <Stroke {...p} fill="currentColor" strokeWidth={0}>
    <rect x="4" y="12" width="3.5" height="8" rx="1" />
    <rect x="10.25" y="7" width="3.5" height="13" rx="1" />
    <rect x="16.5" y="3" width="3.5" height="17" rx="1" />
  </Stroke>
);

export const IconNetwork = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="6" cy="6" r="2.6" /><circle cx="18" cy="6" r="2.6" /><circle cx="12" cy="18" r="2.6" />
    <line x1="8.3" y1="6" x2="15.7" y2="6" /><line x1="7" y1="8.2" x2="10.8" y2="15.8" /><line x1="17" y1="8.2" x2="13.2" y2="15.8" />
  </Stroke>
);

export const IconKey = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="8" cy="8" r="4.2" />
    <line x1="11" y1="11" x2="20" y2="20" />
    <line x1="15.5" y1="15.5" x2="18" y2="13" />
    <line x1="17.5" y1="17.5" x2="20" y2="15" />
  </Stroke>
);

export const IconSettings = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="12" cy="12" r="3" /><circle cx="12" cy="12" r="7" />
    <line x1="19" y1="12" x2="21" y2="12" /><line x1="15.5" y1="18.1" x2="16.5" y2="19.8" />
    <line x1="8.5" y1="18.1" x2="7.5" y2="19.8" /><line x1="5" y1="12" x2="3" y2="12" />
    <line x1="8.5" y1="5.9" x2="7.5" y2="4.2" /><line x1="15.5" y1="5.9" x2="16.5" y2="4.2" />
  </Stroke>
);

export const IconSun = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="12" cy="12" r="4" />
    <line x1="12" y1="2.5" x2="12" y2="4.5" /><line x1="12" y1="19.5" x2="12" y2="21.5" />
    <line x1="4.2" y1="4.2" x2="5.6" y2="5.6" /><line x1="18.4" y1="18.4" x2="19.8" y2="19.8" />
    <line x1="2.5" y1="12" x2="4.5" y2="12" /><line x1="19.5" y1="12" x2="21.5" y2="12" />
    <line x1="4.2" y1="19.8" x2="5.6" y2="18.4" /><line x1="18.4" y1="5.6" x2="19.8" y2="4.2" />
  </Stroke>
);

export const IconMoon = (p: IconProps) => (
  <svg viewBox="0 0 24 24" width={18} height={18} {...p}>
    <circle cx="12" cy="12" r="8" fill="currentColor" />
    <circle cx="16.5" cy="8.5" r="7" fill="var(--bg)" />
  </svg>
);

export const IconLogOut = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M9 4H5.5A1.5 1.5 0 0 0 4 5.5v13A1.5 1.5 0 0 0 5.5 20H9" />
    <line x1="20" y1="12" x2="10.5" y2="12" /><polyline points="16,7.5 20,12 16,16.5" />
  </Stroke>
);

/** Brand mark: gradient tile with a terminal-prompt glyph. Reads as "dev tool", not a stock emoji. */
export function BrandMark({ size = 38 }: { size?: number }) {
  const id = "dp-grad";
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="11" fill={`url(#${id})`} />
      <path d="M13.5 14 L20 20 L13.5 26" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <line x1="22.5" y1="26" x2="27.5" y2="26" stroke="white" strokeWidth="2.6" strokeLinecap="round" />
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop stopColor="#7C6CFF" />
          <stop offset="1" stopColor="#22D3EE" />
        </linearGradient>
      </defs>
    </svg>
  );
}
