import { defaultLocale, formatTime, isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, radii } from '@taskbeavo/ui-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, View } from 'react-native';
import { Button, Card, Heading, Note } from '../../components/ui';
import { Text } from '../../components/ui/text';
import { textStyle } from '../../components/ui/text-style';
import {
  TIME_WINDOWS,
  addDays,
  lastBookableDay,
  windowOpen,
  windowRange,
  zagrebDay,
  type TimeWindow,
} from '../../tasks/post-task-model';
import { Choice, ChoiceGroup } from './choice';
import { ProblemText, problemFor, type StepProps } from './simple-steps';

const WINDOWS = Object.keys(TIME_WINDOWS) as TimeWindow[];

/** Mockup 03, 3e: a day (today, tomorrow or one from the calendar) and a time window, or flexible. */
export function WhenStep({ draft, change, problems, now }: StepProps & { now: Date }) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const today = zagrebDay(now);
  const tomorrow = addDays(today, 1);
  const [picking, setPicking] = useState(
    draft.dateMode === 'date' &&
      draft.date !== null &&
      draft.date !== today &&
      draft.date !== tomorrow,
  );
  const problem = problemFor(
    problems,
    'postTask.errors.when',
    'postTask.errors.tooFarAhead',
    'postTask.errors.timeWindow',
    'postTask.errors.windowPassed',
  );
  const dated = draft.dateMode === 'date';

  function pickDay(date: string) {
    const keep = draft.timeWindow && windowOpen(date, draft.timeWindow, now);
    change({ dateMode: 'date', date, timeWindow: keep ? draft.timeWindow : null });
  }

  return (
    <>
      <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.when.heading')}</Heading>
      <ChoiceGroup label={t('postTask.when.heading')}>
        <Choice
          label={t('postTask.when.today')}
          selected={dated && !picking && draft.date === today}
          onPress={() => {
            setPicking(false);
            pickDay(today);
          }}
          testID="post-when-today"
        />
        <Choice
          label={t('postTask.when.tomorrow')}
          selected={dated && !picking && draft.date === tomorrow}
          onPress={() => {
            setPicking(false);
            pickDay(tomorrow);
          }}
          testID="post-when-tomorrow"
        />
        <Choice
          label={t('postTask.when.pickDate')}
          selected={dated && picking}
          onPress={() => {
            setPicking(true);
            change({ dateMode: 'date' });
          }}
          testID="post-when-pick"
        />
        <Choice
          label={t('postTask.when.flexible')}
          selected={draft.dateMode === 'flexible'}
          onPress={() => {
            setPicking(false);
            change({ dateMode: 'flexible', date: null, timeWindow: null });
          }}
          testID="post-when-flexible"
        />
      </ChoiceGroup>
      {draft.dateMode === 'flexible' ? (
        <Text style={[textStyle('small'), styles.muted]}>{t('postTask.when.flexibleHint')}</Text>
      ) : null}
      {dated && picking ? (
        <Calendar
          selected={draft.date}
          first={today}
          last={lastBookableDay(now)}
          locale={locale}
          onPick={pickDay}
        />
      ) : null}
      {dated && draft.date ? (
        <>
          <Text style={[textStyle('label'), styles.ink]}>{t('postTask.when.windowLabel')}</Text>
          <ChoiceGroup label={t('postTask.when.windowLabel')}>
            {WINDOWS.map((window) => {
              const range = windowRange(draft.date!, window);
              return (
                <Choice
                  key={window}
                  label={t(`postTask.when.windows.${window}`, {
                    start: formatTime(range.start, locale),
                    end: formatTime(range.end, locale),
                  })}
                  selected={draft.timeWindow === window}
                  disabled={!windowOpen(draft.date!, window, now)}
                  onPress={() => change({ timeWindow: window })}
                  testID={`post-window-${window}`}
                />
              );
            })}
          </ChoiceGroup>
        </>
      ) : null}
      {problem ? <ProblemText text={t(problem)} testID="post-when-error" /> : null}
      <Note>{t('postTask.when.limit')}</Note>
    </>
  );
}

function monthOf(day: string): [number, number] {
  const [y, m] = day.split('-').map(Number) as [number, number];
  return [y, m - 1];
}

function Calendar({
  selected,
  first,
  last,
  locale,
  onPick,
}: {
  selected: string | null;
  first: string;
  last: string;
  locale: Locale;
  onPick(day: string): void;
}) {
  const { t } = useTranslation();
  const [[year, month], setMonth] = useState(monthOf(selected ?? first));
  const shown = `${year}-${String(month + 1).padStart(2, '0')}`;
  const title = new Intl.DateTimeFormat(locale, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month, 1)));
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' });
  const full = new Intl.DateTimeFormat(locale, { dateStyle: 'full', timeZone: 'UTC' });
  // 1 January 2024 was a Monday; weeks start on Monday in all four languages' countries.
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    weekday.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );
  const lead = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7;
  const length = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (string | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length }, (_, i) => `${shown}-${String(i + 1).padStart(2, '0')}`),
  ];
  const step = (by: number) =>
    setMonth(([y, m]) => [y + Math.floor((m + by) / 12), (m + by + 12) % 12]);

  return (
    // No side padding: seven 44pt days still fit a 360pt-wide phone (hard rule i).
    <Card testID="post-calendar" style={styles.calendar}>
      <View style={styles.monthRow}>
        <Button
          size="sm"
          variant="ghost"
          label="‹"
          accessibilityLabel={t('postTask.when.previousMonth')}
          disabled={shown <= first.slice(0, 7)}
          onPress={() => step(-1)}
          testID="post-calendar-previous"
        />
        <Text accessibilityRole="header" style={[textStyle('label'), styles.ink]}>
          {title}
        </Text>
        <Button
          size="sm"
          variant="ghost"
          label="›"
          accessibilityLabel={t('postTask.when.nextMonth')}
          disabled={shown >= last.slice(0, 7)}
          onPress={() => step(1)}
          testID="post-calendar-next"
        />
      </View>
      <View
        style={styles.grid}
        importantForAccessibility="no-hide-descendants"
        accessibilityElementsHidden
      >
        {weekdays.map((name) => (
          <Text key={name} style={[textStyle('caption'), styles.cell, styles.muted]}>
            {name}
          </Text>
        ))}
      </View>
      <View style={styles.grid}>
        {cells.map((day, index) => {
          if (!day) return <View key={`blank-${index}`} style={styles.cell} />;
          const open = day >= first && day <= last;
          const on = day === selected;
          return (
            <Pressable
              key={day}
              accessibilityRole="button"
              accessibilityLabel={full.format(new Date(`${day}T12:00:00Z`))}
              accessibilityState={{ selected: on, disabled: !open }}
              disabled={!open}
              onPress={() => onPick(day)}
              style={[styles.cell, styles.day, on && styles.dayOn]}
              testID={`post-day-${day}`}
            >
              <Text
                style={[textStyle('small'), on ? styles.white : open ? styles.ink : styles.faded]}
              >
                {Number(day.slice(8))}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  // Past and too-far days: still AA on white, and marked disabled for screen readers.
  faded: { color: colors.inkMuted, textDecorationLine: 'line-through' },
  white: { color: colors.white },
  calendar: { paddingHorizontal: 0 },
  monthRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: {
    width: `${100 / 7}%`,
    alignItems: 'center',
    justifyContent: 'center',
    textAlign: 'center',
  },
  day: { minHeight: minTapTarget, borderRadius: radii.pill },
  dayOn: { backgroundColor: colors.green },
});
