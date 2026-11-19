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

/** The small interface the wizard needs, so another geocoder can replace Nominatim. */
export interface AddressSearch {
  search(query: string, locale: Locale): Promise<AddressResult[]>;
  /** Shown next to the results, as the provider's terms ask. */
  attribution: string;
}

export const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search';
/** Generic and identifying, as Nominatim's usage policy asks; no person's name or email. */
export const NOMINATIM_USER_AGENT = 'TaskBeavo-showcase/1.0';
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
 * OpenStreetMap Foundation's servers. Usage policy: the wizard searches only when the poster
 * taps Search or submits and shows the attribution; here, requests go one at a time and at
 * least a second apart (a quick second tap waits its turn). Throws when the
 * service can't be reached, so the wizard can offer a retry; the draft stays saved.
 */
export function nominatimSearch(
  request: typeof fetch = fetch,
  clock: Clock = systemClock,
): AddressSearch {
  let lastRequestAt = -Infinity;
  let queue: Promise<void> = Promise.resolve();

  /** Resolves when this request may go: after the one before it, and a second after it. */
  function turn(): Promise<void> {
    const mine = queue.then(async () => {
      const wait = lastRequestAt + MIN_REQUEST_INTERVAL_MS - clock.now();
      if (wait > 0) await clock.sleep(wait);
      lastRequestAt = clock.now();
    });
    queue = mine.catch(() => undefined);
    return mine;
  }

  return {
    attribution: '© OpenStreetMap contributors',
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
      await turn();
      const response = await request(`${NOMINATIM_URL}?${params}`, {
        headers: { Accept: 'application/json', 'User-Agent': NOMINATIM_USER_AGENT },
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
    },
  };
}
