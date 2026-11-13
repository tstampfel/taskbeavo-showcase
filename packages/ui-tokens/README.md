# @taskbeavo/ui-tokens

Brand and component tokens shared by `apps/web` and `apps/mobile`. The base components that use them are:

| Component | Web (`apps/web/src/components/ui`)                                                 | Mobile (`apps/mobile/src/components/ui`)                                                             |
| --------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Button    | `<button>`, variants `primary`, `secondary`, `ghost`, `danger`; sizes `md`, `sm`   | `Pressable` with the same variants and sizes, plus `busy`                                            |
| Input     | `<label>` + `<input>`, hint and error linked by `aria-describedby`, `aria-invalid` | `TextInput` with `accessibilityLabel`; hint and error as `accessibilityHint`, error in a live region |
| Card      | `raised` (shadow) or `flat` (outline), optional `selected`                         | same                                                                                                 |
| Badge     | tones `green`, `orange`, `grey`, `red`                                             | same                                                                                                 |
| Sheet     | native modal `<dialog>`: focus trap, Escape, backdrop click                        | `Modal` with scrim, close button, Android back, scrolling content                                    |

The components hard-code no text: every label, hint, error, title and close label is a prop, already translated by the caller through `@taskbeavo/i18n`.

Web reads the tokens as CSS custom properties (`cssVariables()`, set on `<body>` in `app/[locale]/layout.tsx`); mobile imports them directly. A test on each side fails if a component uses a colour that isn't a token (`apps/web/src/components/ui/ui.test.tsx`, `apps/mobile/__tests__/ui.test.tsx`).

## Contrast (WCAG 2.2 AA)

Every pair a base component draws is in `contrastPairs`, and `src/index.test.ts` checks each against its minimum: 4.5:1 for text (1.4.3), 3:1 for control boundaries and focus indicators (1.4.11). Add a pair there whenever a component starts drawing a new combination.

| Pair                       | Foreground | Background | Ratio   | Minimum |
| -------------------------- | ---------- | ---------- | ------- | ------- |
| body text on page          | `#17211C`  | `#F5F4EF`  | 15.01:1 | 4.5:1   |
| body text on card          | `#17211C`  | `#FFFFFF`  | 16.53:1 | 4.5:1   |
| muted text on page         | `#5E6661`  | `#F5F4EF`  | 5.37:1  | 4.5:1   |
| muted text on card         | `#5E6661`  | `#FFFFFF`  | 5.92:1  | 4.5:1   |
| error text on page         | `#B23A26`  | `#F5F4EF`  | 5.41:1  | 4.5:1   |
| error text on card         | `#B23A26`  | `#FFFFFF`  | 5.96:1  | 4.5:1   |
| orange text on page        | `#9A5E0C`  | `#F5F4EF`  | 4.79:1  | 4.5:1   |
| orange text on card        | `#9A5E0C`  | `#FFFFFF`  | 5.28:1  | 4.5:1   |
| link text on page          | `#1F5C46`  | `#F5F4EF`  | 7.12:1  | 4.5:1   |
| link text on card          | `#1F5C46`  | `#FFFFFF`  | 7.84:1  | 4.5:1   |
| off-white on green         | `#F5F4EF`  | `#1F5C46`  | 7.12:1  | 4.5:1   |
| primary button label       | `#FFFFFF`  | `#1F5C46`  | 7.84:1  | 4.5:1   |
| secondary button label     | `#1F5C46`  | `#FFFFFF`  | 7.84:1  | 4.5:1   |
| ghost button label on page | `#1F5C46`  | `#F5F4EF`  | 7.12:1  | 4.5:1   |
| ghost button label on card | `#1F5C46`  | `#FFFFFF`  | 7.84:1  | 4.5:1   |
| danger button label        | `#B23A26`  | `#FFFFFF`  | 5.96:1  | 4.5:1   |
| disabled button label      | `#5E6661`  | `#E3E1D8`  | 4.52:1  | 4.5:1   |
| green badge                | `#1F5C46`  | `#E3EFE9`  | 6.64:1  | 4.5:1   |
| orange badge               | `#8A5208`  | `#FCEBD2`  | 5.46:1  | 4.5:1   |
| grey badge                 | `#4A524D`  | `#ECEBE5`  | 6.75:1  | 4.5:1   |
| red badge                  | `#A3361F`  | `#F7E1DC`  | 5.40:1  | 4.5:1   |
| input text                 | `#17211C`  | `#FFFFFF`  | 16.53:1 | 4.5:1   |
| input placeholder          | `#5E6661`  | `#FFFFFF`  | 5.92:1  | 4.5:1   |
| input border on page       | `#7D8580`  | `#F5F4EF`  | 3.44:1  | 3:1     |
| input border on card       | `#7D8580`  | `#FFFFFF`  | 3.79:1  | 3:1     |
| focus ring on page         | `#1F5C46`  | `#F5F4EF`  | 7.12:1  | 3:1     |
| focus ring on card         | `#1F5C46`  | `#FFFFFF`  | 7.84:1  | 3:1     |
| error border on page       | `#B23A26`  | `#F5F4EF`  | 5.41:1  | 3:1     |
| error border on card       | `#B23A26`  | `#FFFFFF`  | 5.96:1  | 3:1     |

### Where the tokens differ from the mockups

`design/mockups/base.css` had four pairs below AA. The tokens fix them, and `base.css` now uses the same values:

| Use                       | Mockup             | Ratio               | Token                  | Ratio  |
| ------------------------- | ------------------ | ------------------- | ---------------------- | ------ |
| Error text, danger button | `#C8452F`          | 4.39:1 on off-white | `error` `#B23A26`      | 5.41:1 |
| Orange text (prices)      | `#C77A12`          | 3.07:1 on off-white | `orangeText` `#9A5E0C` | 4.79:1 |
| Disabled button           | white on `#C9D3CE` | 1.53:1              | `inkMuted` on `line`   | 4.52:1 |
| Input border              | `#E3E1D8`          | 1.31:1 on white     | `control` `#7D8580`    | 3.79:1 |

Brand orange `#F2A33A` is 1.89:1 on off-white: use it for fills, icons and stars, never for text. `line` `#E3E1D8` is only for decorative dividers and card outlines.

## Tap targets

- `minTapTarget` is 44 (hard rule i). Every control height token (`controlHeight.md` 52, `controlHeight.sm` 44) is at least that, which the token tests check.
- Buttons have `min-height`/`minHeight` and `min-width`/`minWidth` of at least 44, never a fixed height, so they grow with text. The Sheet close button is 44×44.
- Mobile tests assert the 44pt minimum on Button (both sizes), Input and the Sheet close button.
- Badges are not interactive and so have no tap target.

## Large text

- Web font sizes are in `rem` (`rem()`), so they follow the browser's text size; heights are minimums and labels wrap (`white-space: normal`) rather than truncate.
- Mobile never sets `allowFontScaling={false}` or `numberOfLines` on component text, so text follows the system font scale and wraps. The Sheet body scrolls, so it still fits at the largest size. Tests check the Button label has neither.
- Every text style's line height is at least its font size.
