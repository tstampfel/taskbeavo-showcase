import {
  badgeTones,
  buttonDisabled,
  buttonVariants,
  colors,
  inputColors,
  minTapTarget,
} from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactElement } from 'react';
import { Dimensions, StyleSheet, Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Badge, Button, Card, Input, Note, Screen, Sheet } from '../src/components/ui';
import { topSafeAreaEdge } from '../src/testing/safe-area';
import { fonts } from '../src/fonts';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const renderUi = (node: ReactElement) =>
  render(<SafeAreaProvider initialMetrics={metrics}>{node}</SafeAreaProvider>);

function flat(element: { props: { style?: unknown } }) {
  const style = element.props.style;
  return StyleSheet.flatten(typeof style === 'function' ? style({ pressed: false }) : style) ?? {};
}

describe('Button', () => {
  it('is an accessible button with at least a 44pt target, in both sizes', async () => {
    for (const size of ['md', 'sm'] as const) {
      await renderUi(<Button label="Pošalji ponudu" size={size} onPress={() => {}} />);
      const button = screen.getByRole('button', { name: 'Pošalji ponudu' });
      const style = flat(button);
      expect(style.minHeight).toBeGreaterThanOrEqual(minTapTarget);
      expect(style.minWidth).toBeGreaterThanOrEqual(minTapTarget);
      expect(style.height).toBeUndefined();
    }
  });

  it.each(Object.keys(buttonVariants) as (keyof typeof buttonVariants)[])(
    'draws the %s variant from tokens',
    async (variant) => {
      await renderUi(<Button label="Weiter" variant={variant} />);
      expect(flat(screen.getByRole('button')).backgroundColor).toBe(
        buttonVariants[variant].background,
      );
      expect(flat(screen.getByText('Weiter')).color).toBe(buttonVariants[variant].foreground);
    },
  );

  it('calls onPress', async () => {
    const onPress = jest.fn();
    await renderUi(<Button label="Envoyer" onPress={onPress} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Envoyer' }));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not fire while disabled or busy, and says so', async () => {
    const onPress = jest.fn();
    await renderUi(<Button label="Platiti" busy onPress={onPress} />);
    const button = screen.getByRole('button', { name: 'Platiti' });
    expect(button).toBeDisabled();
    expect(button.props.accessibilityState).toMatchObject({ busy: true, disabled: true });
    expect(flat(button).backgroundColor).toBe(buttonDisabled.background);
    await fireEvent.press(button);
    expect(onPress).not.toHaveBeenCalled();
  });

  it('lets long labels wrap at large text sizes', async () => {
    await renderUi(<Button label="Zahlung freigeben und Bewertung abgeben" />);
    const label = screen.getByText('Zahlung freigeben und Bewertung abgeben');
    expect(label.props.numberOfLines).toBeUndefined();
    expect(label.props.allowFontScaling).not.toBe(false);
    expect(flat(label).fontFamily).toBe(fonts.bold);
  });
});

describe('Input', () => {
  it('is labelled and reads out its hint', async () => {
    await renderUi(<Input label="Cijena" hint="U eurima" testID="price" />);
    const field = screen.getByLabelText('Cijena');
    expect(field.props.accessibilityHint).toBe('U eurima');
    const style = flat(field);
    expect(style.minHeight).toBeGreaterThanOrEqual(minTapTarget);
    expect(style.borderColor).toBe(inputColors.border);
  });

  it('shows the focus border while focused', async () => {
    await renderUi(<Input label="Name" />);
    const field = screen.getByLabelText('Name');
    await fireEvent(field, 'focus');
    expect(flat(screen.getByLabelText('Name')).borderColor).toBe(inputColors.focusBorder);
    await fireEvent(field, 'blur');
    expect(flat(screen.getByLabelText('Name')).borderColor).toBe(inputColors.border);
  });

  it('shows and announces an error', async () => {
    await renderUi(<Input label="Prix" hint="En euros" error="Saisissez un prix" testID="price" />);
    const field = screen.getByLabelText('Prix');
    expect(field.props.accessibilityHint).toBe('Saisissez un prix. En euros');
    expect(flat(field).borderColor).toBe(inputColors.error);
    const error = screen.getByTestId('price-error');
    expect(error).toHaveTextContent('Saisissez un prix');
    expect(error.props.accessibilityLiveRegion).toBe('polite');
  });

  it('passes typing through', async () => {
    const onChangeText = jest.fn();
    await renderUi(<Input label="Ime" onChangeText={onChangeText} />);
    await fireEvent.changeText(screen.getByLabelText('Ime'), 'Ana');
    expect(onChangeText).toHaveBeenCalledWith('Ana');
  });
});

describe('Card and Badge', () => {
  it('render their content from tokens', async () => {
    await renderUi(
      <Card testID="card" selected>
        <Text>Zadatak</Text>
        <Badge label="Plaćeno" tone="green" testID="badge" />
      </Card>,
    );
    expect(screen.getByText('Zadatak')).toBeOnTheScreen();
    expect(flat(screen.getByTestId('card')).borderColor).toBe(colors.green);
    expect(flat(screen.getByTestId('badge')).backgroundColor).toBe(badgeTones.green.background);
    expect(flat(screen.getByText('Plaćeno')).color).toBe(badgeTones.green.foreground);
  });
});

describe('Sheet', () => {
  it('shows a titled panel and closes from the button and the backdrop', async () => {
    const onClose = jest.fn();
    await renderUi(
      <Sheet visible onClose={onClose} title="Filter" closeLabel="Schließen" testID="sheet">
        <Text>Inhalt</Text>
      </Sheet>,
    );
    expect(screen.getByRole('header', { name: 'Filter' })).toBeOnTheScreen();
    expect(screen.getByText('Inhalt')).toBeOnTheScreen();
    expect(screen.getByTestId('sheet').props.accessibilityViewIsModal).toBe(true);

    const close = screen.getByTestId('sheet-close');
    expect(close).toHaveAccessibleName('Schließen');
    expect(flat(close).width).toBeGreaterThanOrEqual(minTapTarget);
    await fireEvent.press(close);
    // The modal panel hides the scrim from iOS VoiceOver; it is still a tap target.
    await fireEvent.press(screen.getByTestId('sheet-scrim', { includeHiddenElements: true }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('renders nothing while hidden', async () => {
    await renderUi(
      <Sheet visible={false} onClose={() => {}} title="Filter" closeLabel="Schließen">
        <Text>Inhalt</Text>
      </Sheet>,
    );
    expect(screen.queryByText('Inhalt')).toBeNull();
  });
});

// BEAV-214: the mockups' top bar, scrolling body and pinned footer.
describe('Screen', () => {
  it('shows the title as the heading, below the status bar', async () => {
    await renderUi(
      <Screen title="Odustani od rezervacije">
        <Text>body</Text>
      </Screen>,
    );
    const title = screen.getByRole('header', { name: 'Odustani od rezervacije' });
    expect(topSafeAreaEdge(title)).toBe('additive');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('has a labelled 44pt back button when it can go back', async () => {
    const onBack = jest.fn();
    await renderUi(
      <Screen title="Checkout" onBack={onBack} backLabel="Zurück zu den Angeboten">
        <Text>body</Text>
      </Screen>,
    );
    const back = screen.getByRole('button', { name: 'Zurück zu den Angeboten' });
    const style = flat(back);
    expect(style.width).toBeGreaterThanOrEqual(minTapTarget);
    expect(style.height).toBeGreaterThanOrEqual(minTapTarget);
    await fireEvent.press(back);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('announces only the labelled back button, not the chevron glyph', async () => {
    await renderUi(
      <Screen title="Checkout" onBack={jest.fn()} backLabel="Back">
        <Text>body</Text>
      </Screen>,
    );
    // VoiceOver stopped on a bare "‹" next to the button (review R1-4).
    const chevron = screen.getByText('‹', { includeHiddenElements: true });
    expect(chevron.props.accessible).toBe(false);
  });

  it('pins the footer outside the scrolling body', async () => {
    // The jest preset reports a font scale of 2; the default text size is 1.
    jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: 1 });
    await renderUi(
      <Screen title="Annuler" testID="s" footer={<Button label="Annuler la réservation" />}>
        <Text>body</Text>
      </Screen>,
    );
    const footer = screen.getByTestId('s-footer');
    expect(screen.getByRole('button', { name: 'Annuler la réservation' })).toBeTruthy();
    for (let node = footer.parent; node; node = node.parent) {
      expect(node.type).not.toBe('RCTScrollView');
    }
    jest.restoreAllMocks();
  });

  it('lets the footer scroll with the body at the largest text sizes', async () => {
    const dimensions = jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: 3.1 });
    try {
      await renderUi(
        <Screen title="Annuler" testID="s" footer={<Button label="Annuler la réservation" />}>
          <Text>body</Text>
        </Screen>,
      );
      let inScroll = false;
      for (let node = screen.getByTestId('s-footer').parent; node; node = node.parent) {
        if (node.type === 'RCTScrollView') inScroll = true;
      }
      expect(inScroll).toBe(true);
    } finally {
      dimensions.mockRestore();
    }
  });
});

describe('Note', () => {
  it('draws its tone from the badge tints', async () => {
    await renderUi(<Note tone="warn">Pazi</Note>);
    expect(flat(screen.getByText('Pazi')).color).toBe(badgeTones.orange.foreground);
  });
});
