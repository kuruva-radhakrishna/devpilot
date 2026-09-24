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

export const IconSend = (p: IconProps) => (
  <Stroke {...p}>
    <line x1="21" y1="3" x2="10.5" y2="13.5" />
    <path d="M21 3 14.5 21 10.5 13.5 3 9.5 21 3z" />
  </Stroke>
);

export const IconBot = (p: IconProps) => (
  <Stroke {...p}>
    <rect x="4" y="8" width="16" height="12" rx="3" />
    <line x1="12" y1="4.5" x2="12" y2="8" />
    <circle cx="12" cy="3.2" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="9" cy="14" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="15" cy="14" r="1.2" fill="currentColor" stroke="none" />
    <line x1="8.5" y1="18" x2="15.5" y2="18" />
  </Stroke>
);

export const IconTrash = (p: IconProps) => (
  <Stroke {...p}>
    <line x1="4" y1="7" x2="20" y2="7" />
    <path d="M6 7 7 20a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
    <line x1="9.5" y1="11" x2="9.5" y2="17" />
    <line x1="14.5" y1="11" x2="14.5" y2="17" />
    <path d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7" />
  </Stroke>
);

export const IconMenu = (p: IconProps) => (
  <Stroke {...p}>
    <line x1="4" y1="6.5" x2="20" y2="6.5" />
    <line x1="4" y1="12" x2="20" y2="12" />
    <line x1="4" y1="17.5" x2="20" y2="17.5" />
  </Stroke>
);

export const IconCheckSmall = (p: IconProps) => (
  <Stroke {...p}><polyline points="5,12.5 9.5,17 19,7" /></Stroke>
);

export const IconXSmall = (p: IconProps) => (
  <Stroke {...p}><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></Stroke>
);

export const IconPlus = (p: IconProps) => (
  <Stroke {...p}><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></Stroke>
);

export const IconEdit = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M15.5 4.5 19.5 8.5 8 20H4v-4L15.5 4.5z" />
    <line x1="13.5" y1="6.5" x2="17.5" y2="10.5" />
  </Stroke>
);

export const IconHelp = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.3 9.6a2.7 2.7 0 0 1 5.2 1c0 1.7-2.2 1.9-2.4 3.4" strokeWidth={1.9} />
    <circle cx="12" cy="17" r="0.9" fill="currentColor" stroke="none" />
  </Stroke>
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
