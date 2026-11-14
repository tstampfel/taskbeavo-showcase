import { spacing, typography } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Dimensions } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { CountryPicker } from '../src/components/sign-in/country-picker';
import { wordFitFontScale } from '../src/components/ui/heading';
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
    await i18n.changeLanguage('de');
    await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <CountryPicker label="Wohnsitzland" value="DE" onChange={jest.fn()} testID="country" />
      </SafeAreaProvider>,
    );
    await fireEvent.press(screen.getByTestId('country'));
    const done = screen.getByRole('button', { name: i18n.t('signIn.countryDone') });
    await fireEvent(done, 'layout', { nativeEvent: { layout: { width: 110, height: 90 } } });

    const title = screen.getByRole('header', { name: 'Wohnsitzland' });
    expect(title.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Wohnsitzland', typography.h2.fontSize, 402 - 2 * spacing.lg - 110),
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
    const done = screen.getByRole('button', { name: i18n.t('signIn.countryDone') });
    await fireEvent(done, 'layout', { nativeEvent: { layout: { width: 220, height: 90 } } });

    const title = screen.getByRole('header', { name: 'Pays de résidence' });
    expect(title.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Pays de résidence', typography.h2.fontSize, 402 - 2 * spacing.lg),
    );
    // The names grow with that title, not with the form behind the modal.
    const name = within(screen.getByTestId('country-FR')).getByText('🇫🇷 France');
    expect(name.props.maxFontSizeMultiplier).toBeGreaterThan(2);
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

    it('finds a country by its name or by its code', async () => {
      await open('fr');
      await fireEvent.changeText(screen.getByTestId('country-search'), 'allem');
      expect(screen.getByTestId('country-DE')).toBeTruthy();
      expect(screen.queryByTestId('country-HR')).toBeNull();
      await fireEvent.changeText(screen.getByTestId('country-search'), 'at');
      expect(screen.getByTestId('country-AT')).toBeTruthy();
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
