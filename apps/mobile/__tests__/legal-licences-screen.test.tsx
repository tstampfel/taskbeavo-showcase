import { LEGAL_PAGES } from '@taskbeavo/app-links';
import { catalogues } from '@taskbeavo/i18n';
import { colors, typography } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Dimensions, StyleSheet } from 'react-native';
import { wordFitFontScale } from '../src/components/ui/heading';
import { i18n } from '../src/i18n';
import { LegalLicencesScreen, MAXMIND_URL } from '../src/screens/legal-licences-screen';
import { SettingsScreen } from '../src/screens/settings-screen';
import { PAGE_HEADING_INSET, TITLE_MAX_FONT_SCALE } from '../src/screens/sign-in-screen';

const en = catalogues.en;

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
    await render(
      <SettingsScreen
        onBack={jest.fn()}
        onBusiness={jest.fn()}
        onPrivacy={jest.fn()}
        onLegal={onLegal}
      />,
    );

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

// BEAV-215: Settings and Legal draw their rows as the mockups' list card (21d).
describe('Settings and Legal design', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('lists the settings in one card, split by hairlines', async () => {
    await i18n.changeLanguage('en');
    await render(
      <SettingsScreen
        onBack={jest.fn()}
        onBusiness={jest.fn()}
        onPrivacy={jest.fn()}
        onLegal={jest.fn()}
      />,
    );
    const list = screen.getByTestId('settings-list');
    expect(within(list).getAllByRole('button')).toHaveLength(3);
    expect(screen.queryByText('›', { includeHiddenElements: true })).toBeNull();
    expect(StyleSheet.flatten(screen.getByTestId('settings-legal').props.style)).toMatchObject({
      borderTopColor: colors.line,
    });
  });

  it('lists the legal documents in one card, with a muted section label', async () => {
    await i18n.changeLanguage('en');
    await renderScreen();
    const list = screen.getByTestId('legal-licences-documents');
    expect(within(list).getAllByRole('link')).toHaveLength(LEGAL_PAGES.length);
    const label = screen.getByRole('header', { name: en.settings.documents });
    expect(StyleSheet.flatten(label.props.style)).toMatchObject({
      color: colors.inkMuted,
      textTransform: 'uppercase',
    });
    expect(screen.getByText(en.settings.languagesNote)).toBeOnTheScreen();
  });

  it('lets the section labels grow past the page-title cap at large text', async () => {
    await i18n.changeLanguage('en');
    await renderScreen();
    const label = screen.getByRole('header', { name: en.settings.licences });
    expect(label.props.maxFontSizeMultiplier).toBeGreaterThan(TITLE_MAX_FONT_SCALE);
  });

  it('fits the section label to its longest word as drawn, in capitals', async () => {
    await i18n.changeLanguage('de');
    await renderScreen();
    const text = catalogues.de.settings.documents;
    const label = screen.getByRole('header', { name: text });
    const width = Dimensions.get('window').width - PAGE_HEADING_INSET;
    expect(label.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale(text.toUpperCase(), typography.caption.fontSize, width),
    );
  });
});
