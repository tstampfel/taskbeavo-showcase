import { spacing, typography } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen } from '@testing-library/react-native';
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
    await fireEvent(done, 'layout', { nativeEvent: { layout: { width: 150, height: 90 } } });

    const title = screen.getByRole('header', { name: 'Wohnsitzland' });
    expect(title.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Wohnsitzland', typography.h2.fontSize, 402 - 2 * spacing.lg - 150),
    );
  });
});
