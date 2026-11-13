import { currentSchedule, loadFees } from '../../../components/fees/fee-schedules';
import { priceListCsv } from '../../../components/fees/price-list';

/** `pricelist.csv` (the list charged now) or `pricelist-<version>.csv` (any published one). */
const FILE = /^pricelist(?:-([A-Za-z0-9._-]{1,32}))?\.csv$/;

/**
 * The machine-readable price list (NN 101/2026, BEAV-191) at /fees/pricelist.csv, outside the
 * locale prefix (the proxy skips paths with a file extension). Built from the API's
 * serviceFeeSchedules, the same data as /{locale}/fees.
 */
export async function GET(_request: Request, { params }: RouteContext<'/fees/[file]'>) {
  const match = FILE.exec((await params).file);
  if (!match) return new Response('Not found', { status: 404 });
  const { schedules } = await loadFees();
  const version = match[1] ?? currentSchedule(schedules).version;
  const csv = priceListCsv(schedules, version);
  if (csv === null) return new Response('Not found', { status: 404 });
  return new Response(csv, {
    headers: {
      'content-type': 'text/csv; charset=utf-8',
      'content-disposition': `inline; filename="taskbeavo-pricelist-${version}.csv"`,
      'cache-control': 'public, max-age=300',
    },
  });
}
