import type { OfferFieldsFragment } from '@taskbeavo/graphql';
import { colors, spacing } from '@taskbeavo/ui-tokens';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';
import { textStyle } from './ui/text-style';

export type TraderFields = Pick<
  OfferFieldsFragment,
  'helperIsTrader' | 'helperBusinessName' | 'helperBusinessOib' | 'helperBusinessAddress'
>;

/**
 * Who the poster contracts with, next to the trader label (hard rule e, BEAV-192): a trader
 * helper's business name, OIB and registered business address, and in-app chat as the rapid
 * means of contact (CRD Art. 6(1)(b)-(c), ZZP čl. 61(1)). For a private helper, with
 * `privateNotice`, the line that EU consumer rights don't cover the task. The API sends the
 * business fields only for a trader, so a private helper's details can never show here, and
 * the address only to the task's poster (often the obrt owner's home, decision 2026-11-07).
 */
export function TraderIdentity({
  offer,
  privateNotice = false,
}: {
  offer: TraderFields;
  privateNotice?: boolean;
}) {
  const { t } = useTranslation();
  if (!offer.helperIsTrader) {
    return privateNotice ? (
      <Text style={[textStyle('small'), styles.muted]} testID="private-notice">
        {t('offers.trader.privateNotice')}
      </Text>
    ) : null;
  }
  const {
    helperBusinessName: name,
    helperBusinessOib: oib,
    helperBusinessAddress: address,
  } = offer;
  return (
    <View testID="trader-identity" style={styles.lines}>
      {name && oib ? (
        <Text style={[textStyle('small'), styles.ink]}>
          {t('offers.trader.identity', { name, oib })}
        </Text>
      ) : null}
      {address ? (
        <Text style={[textStyle('small'), styles.muted]}>
          {t('offers.trader.address', { address })}
        </Text>
      ) : null}
      <Text style={[textStyle('small'), styles.muted]}>{t('offers.trader.contact')}</Text>
    </View>
  );
}

/** The compare table's one-line cell: the business and OIB, or "Private individual". */
export function traderSummary(offer: TraderFields, t: TFunction) {
  const { helperBusinessName: name, helperBusinessOib: oib } = offer;
  if (!offer.helperIsTrader) return t('offers.trader.private');
  return name && oib ? t('offers.trader.identity', { name, oib }) : t('offers.trader.business');
}

const styles = StyleSheet.create({
  lines: { gap: spacing.xs },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
});
