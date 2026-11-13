import { ServiceFeeSchedulesDocument } from '@taskbeavo/graphql';
import { connection } from 'next/server';
import { query } from '../../apollo/rsc';
import type { CancellationAmounts, FeeSchedule } from './fees-view';

export interface FeesData {
  /** Every service fee price list, newest effective date first. */
  schedules: FeeSchedule[];
  /** The cancellation policy's numbers, for the refund table. */
  cancellation: CancellationAmounts;
}

/**
 * The price lists and the cancellation numbers from the API. Rendered per request (never at
 * build time), so an announced list turns current on its effective date.
 */
export async function loadFees(): Promise<FeesData> {
  await connection();
  const { data } = await query({ query: ServiceFeeSchedulesDocument });
  if (!data) throw new Error('The service fee query returned no data.');
  return { schedules: data.serviceFeeSchedules, cancellation: data.cancellationAmounts };
}

export function currentSchedule(schedules: FeeSchedule[]): FeeSchedule {
  const current = schedules.find((schedule) => schedule.status === 'CURRENT');
  if (!current) throw new Error('The API returned no current service fee schedule.');
  return current;
}
