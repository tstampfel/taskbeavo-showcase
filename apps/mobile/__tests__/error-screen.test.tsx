import { catalogues, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { ErrorScreen } from '../src/components/error-screen';
import { i18n } from '../src/i18n';

describe('ErrorScreen', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('shows the error and a retry button in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    const text = catalogues[locale].errorBoundary;
    const onRetry = jest.fn();
    await render(<ErrorScreen onRetry={onRetry} />);

    expect(screen.getByRole('header')).toHaveTextContent(text.title);
    expect(screen.getByText(text.body)).toBeOnTheScreen();
    fireEvent.press(screen.getByRole('button', { name: text.retry }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
