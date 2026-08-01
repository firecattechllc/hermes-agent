---
name: Sigil Mission Control
colors:
  surface: '#121415'
  surface-dim: '#121415'
  surface-bright: '#38393a'
  surface-container-lowest: '#0c0e0f'
  surface-container-low: '#1a1c1d'
  surface-container: '#1e2021'
  surface-container-high: '#282a2b'
  surface-container-highest: '#333536'
  on-surface: '#e2e2e3'
  on-surface-variant: '#bbcabf'
  inverse-surface: '#e2e2e3'
  inverse-on-surface: '#2f3132'
  outline: '#86948a'
  outline-variant: '#3c4a42'
  surface-tint: '#4edea3'
  primary: '#4edea3'
  on-primary: '#003824'
  primary-container: '#10b981'
  on-primary-container: '#00422b'
  inverse-primary: '#006c49'
  secondary: '#4fdbc8'
  on-secondary: '#003731'
  secondary-container: '#04b4a2'
  on-secondary-container: '#003f38'
  tertiary: '#91db2a'
  on-tertiary: '#1f3700'
  tertiary-container: '#72b400'
  on-tertiary-container: '#254000'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#6ffbbe'
  primary-fixed-dim: '#4edea3'
  on-primary-fixed: '#002113'
  on-primary-fixed-variant: '#005236'
  secondary-fixed: '#71f8e4'
  secondary-fixed-dim: '#4fdbc8'
  on-secondary-fixed: '#00201c'
  on-secondary-fixed-variant: '#005048'
  tertiary-fixed: '#acf847'
  tertiary-fixed-dim: '#91db2a'
  on-tertiary-fixed: '#102000'
  on-tertiary-fixed-variant: '#304f00'
  background: '#121415'
  on-background: '#e2e2e3'
  surface-variant: '#333536'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  data-lg:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '500'
    lineHeight: 24px
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  data-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.06em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 24px
---

## Brand & Style
The design system is engineered for high-stakes financial operations, evoking the controlled atmosphere of a mission control center. It prioritizes clarity, precision, and institutional trust over decorative trends. 

The aesthetic is **Modern-Institutional**, utilizing a dark-mode first architecture to reduce eye strain during long-duration monitoring. The visual language focuses on depth through layering rather than traditional skeuomorphism. Surfaces are treated as physical panels, with precise 1px borders and subtle tonal shifts that define the hierarchy of information. The emotional response is one of calm authority—designed for experts who require a "heads-up display" for complex data environments.

## Colors
This design system utilizes a palette of deep graphite and obsidian tones to establish a grounded, stable environment. 

- **Foundation:** The background (#0A0C0D) provides the "void" upon which all panels sit.
- **Surface Strategy:** Use #121417 for primary content panels and #1A1D21 for floating modals or active selection states.
- **Semantic Accents:** Colors are used functionally, not decoratively. Emerald (#10B981) represents positive growth or active status. Teal (#14B8A6) is reserved for informational highlights and primary actions. 
- **Data Signaling:** Restrained Lime (#84CC16) is used sparingly for "optimal" status indicators to differentiate from standard success.
- **Borders:** All interface elements are bounded by #262A30 to maintain structural definition in low-light conditions.

## Typography
The typography system is split between **Inter** for UI navigation and **JetBrains Mono** for technical data display.

- **Inter:** Use for all administrative text, headlines, and general body copy. It provides a human, readable balance to the technical nature of the application.
- **JetBrains Mono:** Strictly reserved for financial figures, transaction IDs, timestamps, and tabular data. The monospaced nature ensures that columns of numbers align perfectly for rapid scanning.
- **Visual Hierarchy:** Use `label-caps` for secondary headers within cards and table column headers to create a "blueprint" aesthetic.

## Layout & Spacing
The layout follows a **Fluid-Grid Model** designed for maximum information density without visual noise.

- **Grid:** A 12-column grid is used for the primary dashboard. Components should snap to the 4px baseline grid to ensure vertical rhythm.
- **Density:** Financial operations require a "High-Density" approach. Use `md` (16px) for primary container padding and `sm` (8px) for internal element spacing.
- **Modularity:** Content is organized into "Modules." Each module is an independent functional unit. Avoid large areas of "negative space" that force excessive scrolling; instead, use purposeful grouping to keep related data points within the same viewport.

## Elevation & Depth
In this design system, depth is communicated through **Tonal Layering** and **Restrained Glows** rather than soft shadows.

1.  **Level 0 (Base):** Background (#0A0C0D). No interactive elements sit here.
2.  **Level 1 (Panels):** Surface (#121417) with a 1px solid border (#262A30). This is the default state for workspace cards.
3.  **Level 2 (Active/Hover):** Surface-Elevated (#1A1D21). Used when a user interacts with a module or for context menus.
4.  **Signal Depth:** For critical alerts or primary actions, a 2px inner-border or a very subtle outer glow (4px blur, 10% opacity) of the semantic color (e.g., Emerald) can be applied to draw the eye without breaking the flat institutional aesthetic.

## Shapes
The shape language is **Technical and Precise**. 

A "Soft" roundedness (0.25rem/4px) is applied to all standard components (buttons, input fields, panels). This small radius softens the "brutalist" edge enough to feel modern while maintaining a rigid, grid-aligned institutional feel. 

- **Icons:** Use linear, 2px stroke icons with squared ends to match the typographic weight of Inter.
- **Connectors:** Where data points are linked (e.g., flowcharts), use straight lines with 90-degree turns rather than organic curves.

## Components
- **Buttons:** Primary buttons use a solid Teal (#14B8A6) background with Text-Primary. Secondary buttons use an outlined style with #262A30 borders. All buttons have a fixed height (32px for compact, 40px for standard) to maintain the grid.
- **Data Tables:** These are the core of the application. Row lines use #262A30. Alternating row zebra-striping is prohibited; use hover states instead. Column headers must use `label-caps`.
- **Input Fields:** Backgrounds should be #0A0C0D (inset look). On focus, the border shifts to Teal (#14B8A6) with no outer glow.
- **Status Chips:** Use a "pill" shape but with the `rounded-sm` (4px) setting. Chips should use a subtle background tint (10% opacity of the semantic color) and a high-contrast label.
- **Mission Status Bar:** A persistent top-level component that uses a darker-than-base background to frame the workspace, containing global search, system health, and user profile.
- **Monospace Tooltips:** All data-heavy tooltips must use JetBrains Mono and #1A1D21 surfaces to ensure maximum legibility against complex backgrounds.