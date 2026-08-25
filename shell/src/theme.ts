/**
 * Warraq design tokens.
 *
 * Fluent Design, tuned toward the density of Raycast and the calm of Obsidian.
 * Deep teal reads as "document tooling" and stays distinct from Microsoft blue.
 */
import {
  createLightTheme,
  createDarkTheme,
  type BrandVariants,
  type Theme,
} from "@fluentui/react-components";

const warraqBrand: BrandVariants = {
  10: "#020404",
  20: "#0F1B1B",
  30: "#132D2D",
  40: "#153A3A",
  50: "#164848",
  60: "#155656",
  70: "#0E6E6E", // accent
  80: "#127B7B",
  90: "#279090",
  100: "#42A3A3",
  110: "#5FB5B5",
  120: "#7CC6C6",
  130: "#99D6D6",
  140: "#B6E4E4",
  150: "#D2F0F0",
  160: "#EDFAFA",
};

export const lightTheme: Theme = {
  ...createLightTheme(warraqBrand),
  borderRadiusMedium: "8px",
  borderRadiusSmall: "4px",
};

export const darkTheme: Theme = {
  ...createDarkTheme(warraqBrand),
  borderRadiusMedium: "8px",
  borderRadiusSmall: "4px",
};

/** Quality badge colours. Always paired with an icon and a word so the
 *  meaning survives for colour-blind users. */
export const qualityTones = {
  teal: { bg: "#0E6E6E", fg: "#FFFFFF" },
  green: { bg: "#1F7A3D", fg: "#FFFFFF" },
  amber: { bg: "#8A5A00", fg: "#FFFFFF" },
  grey: { bg: "#5A5A5A", fg: "#FFFFFF" },
  red: { bg: "#A4262C", fg: "#FFFFFF" },
} as const;

export type QualityTone = keyof typeof qualityTones;
