import { catalogues, locales } from '@taskbeavo/i18n';
import { minTapTarget } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';
import { LanguageSwitcher } from '../src/components/language-switcher';
import { i18n } from '../src/i18n';

describe('LanguageSwitcher', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('is labelled in %s and marks that language as selected', async (locale) => {
    await i18n.changeLanguage(locale);
    await render(<LanguageSwitcher onChoose={jest.fn()} />);

    // The group itself isn't focusable (that would hide the options on iOS), so find it by id.
    const group = screen.getByTestId('language-switcher');
    expect(group.props.accessibilityRole).toBe('radiogroup');
    expect(group.props.accessibilityLabel).toBe(catalogues[locale].languageSwitcher.label);
    const radios = screen.getAllByRole('radio');
    expect(radios.map((radio) => radio.props.accessibilityLabel)).toEqual([
      'Hrvatski',
      'English',
      'Deutsch',
      'Français',
    ]);
    for (const [index, radio] of radios.entries()) {
      expect(radio.props.accessibilityState.selected).toBe(locales[index] === locale);
    }
  });

  it('reports a new choice and ignores the current one', async () => {
    await i18n.changeLanguage('hr');
    const onChoose = jest.fn();
    await render(<LanguageSwitcher onChoose={onChoose} />);

    await fireEvent.press(screen.getByRole('radio', { name: 'Hrvatski' }));
    await fireEvent.press(screen.getByRole('radio', { name: 'Deutsch' }));

    expect(onChoose).toHaveBeenCalledTimes(1);
    expect(onChoose).toHaveBeenCalledWith('de');
  });

  it('gives every option at least a 44pt tap target', async () => {
    await render(<LanguageSwitcher onChoose={jest.fn()} />);
    for (const radio of screen.getAllByRole('radio')) {
      const style = StyleSheet.flatten(radio.props.style);
      expect(style.minHeight).toBeGreaterThanOrEqual(minTapTarget);
      expect(style.minWidth).toBeGreaterThanOrEqual(minTapTarget);
    }
  });
});
