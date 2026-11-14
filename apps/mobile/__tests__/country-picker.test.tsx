import { minTapTarget, spacing, typography } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Dimensions } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import {
  CountryPicker,
  OPTION_INSET,
  doneButtonWidth,
} from '../src/components/sign-in/country-picker';
import { wordFitFontScale, wordsFit } from '../src/components/ui/heading';
import { i18n } from '../src/i18n';

const metrics = {
  frame: { x: 0, y: 0, width: 402, height: 874 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};

describe('CountryPicker', () => {
  beforeEach(() => {
    jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: 53 / 17 });
  });
  afterEach(() => jest.restoreAllMocks());
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('leaves room on the title line for the Done button as it grows with the text size', async () => {
    jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: 1.3 });
    await i18n.changeLanguage('de');
    await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <CountryPicker label="Wohnsitzland" value="DE" onChange={jest.fn()} testID="country" />
      </SafeAreaProvider>,
    );
    await fireEvent.press(screen.getByTestId('country'));

    const done = doneButtonWidth(i18n.t('signIn.countryDone'), 1.3, 402);
    expect(done).toBeGreaterThan(minTapTarget);
    const title = screen.getByRole('header', { name: 'Wohnsitzland' });
    expect(title.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Wohnsitzland', typography.h2.fontSize, 402 - 2 * spacing.lg - done),
    );
  });

  it('puts the title under Done, on a line of its own, once Done takes a third of the line (BEAV-223)', async () => {
    await i18n.changeLanguage('fr');
    await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <CountryPicker label="Pays de résidence" value="FR" onChange={jest.fn()} testID="country" />
      </SafeAreaProvider>,
    );
    await fireEvent.press(screen.getByTestId('country'));
    const title = screen.getByRole('header', { name: 'Pays de résidence' });
    expect(title.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Pays de résidence', typography.h2.fontSize, 402 - 2 * spacing.lg),
    );
    // The names grow with that title, not with the form behind the modal.
    const name = within(screen.getByTestId('country-FR')).getByText('🇫🇷 France');
    expect(name.props.maxFontSizeMultiplier).toBeGreaterThan(2);
  });

  describe('the header decides its layout before the first paint (R4-1)', () => {
    const narrow = {
      frame: { x: 0, y: 0, width: 375, height: 812 },
      insets: { top: 47, left: 0, right: 0, bottom: 34 },
    };

    async function openAt(
      width: number,
      fontScale: number,
      locale: 'hr' | 'de' | 'en' | 'fr',
      label: string,
    ) {
      jest.spyOn(Dimensions, 'get').mockReturnValue({ width, height: 812, scale: 3, fontScale });
      await i18n.changeLanguage(locale);
      await render(
        <SafeAreaProvider initialMetrics={{ ...narrow, frame: { ...narrow.frame, width } }}>
          <CountryPicker label={label} value="HR" onChange={jest.fn()} testID="country" />
        </SafeAreaProvider>,
      );
      await fireEvent.press(screen.getByTestId('country'));
      return screen.getByRole('header', { name: label });
    }

    it.each([
      [375, 'hr', 'Država u kojoj živite'],
      [390, 'hr', 'Država u kojoj živite'],
      [402, 'hr', 'Država u kojoj živite'],
      [390, 'de', 'Wohnsitzland'],
      [375, 'de', 'Wohnsitzland'],
      [375, 'en', 'Country you live in'],
      [375, 'fr', 'Pays de résidence'],
    ] as const)(
      'at %ipt and AX5 in %s, "%s" starts on its own line under Done, with no layout event',
      async (width, locale, label) => {
        const title = await openAt(width, 53 / 17, locale, label);
        const full = width - 2 * spacing.lg;
        // The whole line from the first render: no second pass that iOS would draw clipped.
        expect(title.props.maxFontSizeMultiplier).toBe(
          wordFitFontScale(label, typography.h2.fontSize, full),
        );
        // Every word of the title fits that line at the size it renders.
        const scale = Math.min(53 / 17, title.props.maxFontSizeMultiplier) * (1 - 1e-9);
        expect(wordsFit(label, typography.h2.fontSize * scale, full)).toBe(true);
      },
    );

    it('keeps the title beside Done at the default text size', async () => {
      const label = 'Država u kojoj živite';
      const title = await openAt(375, 1, 'hr', label);
      const done = doneButtonWidth(i18n.t('signIn.countryDone'), 1, 375);
      expect(title.props.maxFontSizeMultiplier).toBe(
        wordFitFontScale(label, typography.h2.fontSize, 375 - 2 * spacing.lg - done),
      );
    });
  });

  describe('the list (BEAV-223)', () => {
    async function open(locale: 'hr' | 'en' | 'de' | 'fr') {
      await i18n.changeLanguage(locale);
      await render(
        <SafeAreaProvider initialMetrics={metrics}>
          <CountryPicker label="Country" value="HR" onChange={jest.fn()} testID="country" />
        </SafeAreaProvider>,
      );
      await fireEvent.press(screen.getByTestId('country'));
    }

    it.each([
      ['hr', 'Njemačka'],
      ['en', 'Germany'],
      ['de', 'Deutschland'],
      ['fr', 'Allemagne'],
    ] as const)('names each country in %s, with its code beside it', async (locale, germany) => {
      await open(locale);
      const option = screen.getByTestId('country-DE');
      expect(option.props.accessibilityLabel).toBe(germany);
      expect(within(option).getByText(`🇩🇪 ${germany}`)).toBeTruthy();
      expect(within(option).getByText('DE')).toBeTruthy();
    });

    it('keeps each name whole, so one long name doesn’t hold the list small (R2-1)', async () => {
      await open('de');
      await fireEvent.changeText(screen.getByTestId('country-search'), 'hongkong');
      const long = within(screen.getByTestId('country-HK')).getByText(/Hongkong/);
      const text = long.props.children as string;
      const width = 402 - OPTION_INSET;
      expect(long.props.maxFontSizeMultiplier).toBeLessThanOrEqual(
        wordFitFontScale(text, typography.body.fontSize, width) + 1e-9,
      );
      await fireEvent.changeText(screen.getByTestId('country-search'), '');
      const germany = within(screen.getByTestId('country-DE')).getByText('🇩🇪 Deutschland');
      expect(germany.props.maxFontSizeMultiplier).toBeGreaterThan(2);
    });

    it('finds a country by its name or by its code', async () => {
      await open('fr');
      await fireEvent.changeText(screen.getByTestId('country-search'), 'allem');
      expect(screen.getByTestId('country-DE')).toBeTruthy();
      expect(screen.queryByTestId('country-HR')).toBeNull();
      await fireEvent.changeText(screen.getByTestId('country-search'), 'at');
      expect(screen.getByTestId('country-AT')).toBeTruthy();
      // Without accents (review R1-5).
      await fireEvent.changeText(screen.getByTestId('country-search'), 'algerie');
      expect(screen.getByTestId('country-DZ')).toBeTruthy();
    });

    it('gives the modal its own safe area, so Done stays below the status bar', async () => {
      await open('en');
      // A modal is a separate native window: the screen's provider doesn't reach into it.
      const done = screen.getByRole('button', { name: i18n.t('signIn.countryDone') });
      let node = done.parent;
      const names: string[] = [];
      while (node) {
        const type = node.type as { displayName?: string; name?: string } | string;
        names.push(typeof type === 'string' ? type : (type.displayName ?? type.name ?? ''));
        node = node.parent;
      }
      const modal = names.findIndex((name) => name === 'Modal');
      const provider = names.findIndex((name) => /SafeAreaProvider$/.test(name));
      expect(provider).toBeGreaterThan(-1);
      expect(provider).toBeLessThan(modal);
    });
  });
});
