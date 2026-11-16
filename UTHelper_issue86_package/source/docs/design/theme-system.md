# Theme system

UTHelper uses semantic color roles instead of component-owned palette values. A
component should describe the purpose of a color (`C.SURFACE`, `C.ACCENT`,
`C.ON_ACCENT`, `C.WARNING`, or `C.BORDER_STRONG`) and must not assume that a
specific role is light or dark.

## Maintained contract

- `src/gui/core/theme_presets.py` owns the selectable palettes.
- `src/gui/core/theme.py` maps the selected palette to runtime semantic tokens and
  Flet's `ColorScheme`.
- Solid semantic controls pair their background with the matching foreground:
  `ACCENT/ON_ACCENT`, `CRITICAL/ON_CRITICAL`, `WARNING/ON_WARNING`, and
  `SAFE/ON_SAFE`.
- `SURFACE_ELEVATED`, `BORDER_STRONG`, `SCRIM`, and `SHADOW` represent elevation,
  interactive outlines, modal occlusion, and shadows. They are not interchangeable
  with ordinary card surfaces or dividers.
- A live theme change must update already-mounted controls. Reusable components
  expose `update_theme()` or `refresh_theme()`; `AppController._rebuild_colors()`
  propagates the change.
- The startup splash uses the selected theme. The boot-failure screen deliberately
  keeps a self-contained emergency palette so it can render when config or theme
  initialization itself failed.

## Material Graphite

`material_graphite` is the additional neutral dark preset. It uses layered charcoal
surfaces instead of pure black, a restrained light-blue primary, desaturated status
colors, and explicit foreground roles for filled controls. Existing users retain
their selected theme; the new preset is offered first in the theme selector but is
not forced over saved preferences.

## Accessibility checks

Preset primary/secondary text and every semantic color used as small text must have
at least a 4.5:1 contrast ratio against both the page and card surfaces. Filled
control foreground/background pairs must also meet 4.5:1. These invariants are
executable in `tests/test_theme_accessibility.py`.

Custom colors remain available. Their filled-control foreground is recomputed at
runtime from black and white to preserve legibility; custom authors remain
responsible for contrast when using a color as unfilled text.

The audit follows the role-based theme guidance in Android's design documentation,
Android's dark-theme recommendation to use dark gray tonal surfaces rather than
pure black, and WCAG 2.2 AA contrast rules:

- <https://developer.android.com/design/ui/mobile/guides/styles/themes>
- <https://developer.android.com/develop/ui/views/theming/darktheme>
- <https://developer.android.com/design/ui/mobile/guides/styles/color>
- <https://www.w3.org/WAI/standards-guidelines/act/rules/afw4f7/>

Keyboard focus, TalkBack/VoiceOver announcements, font scaling, and appearance on
physical OLED/LCD panels still require release-candidate checks on real Windows,
Android, and iOS devices; source-level contrast tests do not replace those checks.
