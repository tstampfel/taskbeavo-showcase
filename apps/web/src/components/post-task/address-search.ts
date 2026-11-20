// Adapted from apps/mobile/src/tasks/address-search.ts (BEAV-86) for the browser (BEAV-87).
import type { Locale } from '@taskbeavo/i18n';

/** A place the poster can pick: the address as the geocoder writes it and its real coordinates. */
export interface AddressResult {
  id: string;
  address: string;
  lat: number;
  lng: number;
  /** The town, village or municipality; empty when the geocoder doesn't say. */
  city: string;
}

/**
 * The small interface the wizard needs, so another geocoder can replace Nominatim. The wizard
 * shows the provider's attribution from the catalogue (postTask.where.attribution).
 */
export interface AddressSearch {
  search(query: string, locale: Locale): Promise<AddressResult[]>;
}

export const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search';
export const MIN_QUERY_LENGTH = 3;
/** Nominatim's usage policy: at most one request per second. */
export const MIN_REQUEST_INTERVAL_MS = 1_000;

export interface Clock {
  now(): number;
  sleep(ms: number): Promise<void>;
}

const systemClock: Clock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

interface NominatimPlace {
  place_id?: number | string;
  lat?: string;
  lon?: string;
  display_name?: string;
  address?: Record<string, string | undefined>;
}

/**
 * OpenStreetMap Nominatim (no key needed), limited to Croatia. The address text goes to the
 * OpenStreetMap Foundation's servers, straight from the browser. Usage policy: the wizard
 * searches only when the poster submits and shows the attribution; here, requests go one at a
 * time (the next waits until the one before has answered) and start at least a second apart.
 * Throws when the service can't be reached, so the wizard can offer a retry; the draft stays
 * saved.
 */
export function nominatimSearch(
  request: typeof fetch = fetch,
  clock: Clock = systemClock,
): AddressSearch {
  let lastRequestAt = -Infinity;
  let queue: Promise<void> = Promise.resolve();

  /** Runs `work` once the request before it has finished, and a second after it started. */
  async function inTurn<T>(work: () => Promise<T>): Promise<T> {
    const previous = queue;
    let finished!: () => void;
    queue = new Promise<void>((resolve) => {
      finished = resolve;
    });
    try {
      await previous;
      const wait = lastRequestAt + MIN_REQUEST_INTERVAL_MS - clock.now();
      if (wait > 0) await clock.sleep(wait);
      lastRequestAt = clock.now();
      return await work();
    } finally {
      finished();
    }
  }

  return {
    async search(query, locale) {
      const q = query.trim();
      if (q.length < MIN_QUERY_LENGTH) return [];
      const params = [
        ['q', q],
        ['format', 'jsonv2'],
        ['countrycodes', 'hr'],
        ['addressdetails', '1'],
        ['limit', '5'],
        ['accept-language', locale],
      ]
        .map(([key, value]) => `${key}=${encodeURIComponent(value!)}`)
        .join('&');
      return inTurn(async () => {
        const response = await request(`${NOMINATIM_URL}?${params}`, {
          headers: { Accept: 'application/json' },
          // Browsers can't set User-Agent; the site's origin goes as the Referer, as Nominatim's
          // policy asks of web apps. Nothing personal: no cookies, no path.
          referrerPolicy: 'strict-origin-when-cross-origin',
          credentials: 'omit',
        });
        if (!response.ok) throw new Error(`Address search failed: ${response.status}`);
        const places = (await response.json()) as unknown;
        if (!Array.isArray(places)) return [];
        return (places as NominatimPlace[]).flatMap((place) => {
          const lat = Number(place.lat);
          const lng = Number(place.lon);
          if (!place.display_name || !Number.isFinite(lat) || !Number.isFinite(lng)) return [];
          const parts = place.address ?? {};
          return [
            {
              id: String(place.place_id ?? `${lat},${lng}`),
              address: place.display_name,
              lat,
              lng,
              city: parts.city ?? parts.town ?? parts.village ?? parts.municipality ?? '',
            },
          ];
        });
      });
    },
  };
}
