import { catalogues } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { i18n } from '../src/i18n';
import { LegalLicencesScreen, MAXMIND_URL } from '../src/screens/legal-licences-screen';
import { SettingsScreen } from '../src/screens/settings-screen';

const ENGLISH =
  'This product includes GeoLite Data created by MaxMind, available from https://www.maxmind.com.';

async function renderScreen() {
  const props = { onBack: jest.fn(), openLegal: jest.fn(), openUrl: jest.fn() };
  await render(<LegalLicencesScreen {...props} />);
  return props;
}

/** The attribution sentences in the order they are shown. */
function attribution(): string[] {
  return screen
    .getByTestId('licence-geolite')
    .children.map((child) =>
      typeof child === 'string' ? child : (child.props.testID as string).replace(/-row$/, ''),
    );
}

describe('Settings → Legal and licences', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('opens Legal and licences from Settings', async () => {
    await i18n.changeLanguage('en');
    const onLegal = jest.fn();
    await render(<SettingsScreen onBack={jest.fn()} onBusiness={jest.fn()} onLegal={onLegal} />);

    expect(screen.getByRole('button', { name: 'Legal and licences' })).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('settings-legal'));
    expect(onLegal).toHaveBeenCalled();
  });

  it('shows the English MaxMind attribution alone in English', async () => {
    await i18n.changeLanguage('en');
    await renderScreen();

    expect(screen.getByTestId('licence-geolite-en')).toHaveTextContent(ENGLISH);
    expect(screen.queryByTestId('licence-geolite-local')).toBeNull();
  });

  it('puts the Croatian sentence first in Croatian, the English original after it', async () => {
    await i18n.changeLanguage('hr');
    await renderScreen();

    expect(attribution()).toEqual(['licence-geolite-local', 'licence-geolite-en']);
    expect(screen.getByTestId('licence-geolite-local')).toHaveTextContent(
      'Ovaj proizvod uključuje GeoLite podatke koje je izradio MaxMind, dostupne na https://www.maxmind.com.',
    );
    expect(screen.getByTestId('licence-geolite-en')).toHaveTextContent(ENGLISH);
  });

  it.each(['de', 'fr'] as const)(
    'keeps the English original first in %s, with the translation next to it',
    async (locale) => {
      await i18n.changeLanguage(locale);
      await renderScreen();

      expect(attribution()).toEqual(['licence-geolite-en', 'licence-geolite-local']);
      expect(screen.getByTestId('licence-geolite-en')).toHaveTextContent(ENGLISH);
    },
  );

  it("opens MaxMind's site and the legal pages", async () => {
    await i18n.changeLanguage('de');
    const props = await renderScreen();

    await fireEvent.press(screen.getByTestId('licence-geolite-en-link'));
    // The whole sentence is the tap target, at least 44pt high (review R1-10).
    await fireEvent.press(screen.getByTestId('licence-geolite-en-row'));
    expect(screen.getByTestId('licence-geolite-en-row')).toHaveStyle({ minHeight: 44 });
    expect(props.openUrl).toHaveBeenNthCalledWith(1, MAXMIND_URL);
    expect(props.openUrl).toHaveBeenNthCalledWith(2, MAXMIND_URL);
    expect(
      screen.getByRole('link', { name: catalogues.de.legal.documents.IMPRINT }),
    ).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('legal-licences-imprint'));
    expect(props.openLegal).toHaveBeenCalledWith('imprint', 'de');
  });
});
