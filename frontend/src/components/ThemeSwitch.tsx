import { useState } from "react";
import { IconContrast, IconMonitor, IconMoon, IconSun } from "../icons";

export type ThemeName = "soft" | "light" | "dark" | "auto";

const ORDER: ThemeName[] = ["soft", "light", "dark", "auto"];
const LABEL: Record<ThemeName, string> = { soft: "Soft", light: "Light", dark: "Dark", auto: "Auto" };
const ICON = { soft: IconContrast, light: IconSun, dark: IconMoon, auto: IconMonitor };

function current(): ThemeName {
  const t = document.documentElement.getAttribute("data-theme");
  return (ORDER as string[]).includes(t ?? "") ? (t as ThemeName) : "soft";
}

/** One button that cycles Soft -> Light -> Dark -> Auto, remembering the choice. */
export default function ThemeSwitch() {
  const [theme, setTheme] = useState<ThemeName>(current);
  const Icon = ICON[theme];

  function next() {
    const t = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
    setTheme(t);
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("devpilot_theme", t); } catch { /* storage can be blocked */ }
  }

  return (
    <button
      type="button"
      className="btn-chip"
      onClick={next}
      title={`Theme: ${LABEL[theme]} (click to change)`}
      aria-label={`Theme: ${LABEL[theme]}. Click to change.`}
    >
      <Icon width={16} height={16} />
      <span className="chip-label">{LABEL[theme]}</span>
    </button>
  );
}
