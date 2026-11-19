import {
  MIN_REQUEST_INTERVAL_MS,
  NOMINATIM_USER_AGENT,
  nominatimSearch,
  type Clock,
} from '../src/tasks/address-search';

function answer(body: unknown, ok = true) {
  return jest.fn(async () => ({ ok, status: ok ? 200 : 503, json: async () => body }) as Response);
}

it('searches Croatia only, in the app language, with a generic User-Agent', async () => {
  const request = answer([
    {
      place_id: 7,
      lat: '42.6583',
      lon: '18.0861',
      display_name: 'Ulica Ante Starčevića 1, Dubrovnik, Hrvatska',
      address: { town: 'Dubrovnik', country_code: 'hr' },
    },
    { place_id: 8, lat: 'x', lon: '18', display_name: 'Broken' },
  ]);

  const results = await nominatimSearch(request).search(' Starčevića 1 ', 'de');

  expect(results).toEqual([
    {
      id: '7',
      address: 'Ulica Ante Starčevića 1, Dubrovnik, Hrvatska',
      lat: 42.6583,
      lng: 18.0861,
      city: 'Dubrovnik',
    },
  ]);
  const [url, init] = request.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toContain('https://nominatim.openstreetmap.org/search?q=Star%C4%8Devi%C4%87a%201');
  expect(url).toContain('countrycodes=hr');
  expect(url).toContain('format=jsonv2');
  expect(url).toContain('limit=5');
  expect(url).toContain('accept-language=de');
  expect(init.headers).toMatchObject({ 'User-Agent': NOMINATIM_USER_AGENT });
  expect(NOMINATIM_USER_AGENT).not.toMatch(/@/);
});

it('sends nothing for a query too short to search', async () => {
  const request = answer([]);
  expect(await nominatimSearch(request).search('ab', 'en')).toEqual([]);
  expect(request).not.toHaveBeenCalled();
});

it('throws when the service fails, so the wizard can offer a retry', async () => {
  await expect(nominatimSearch(answer({}, false)).search('Gruž', 'hr')).rejects.toThrow();
});

it('names OpenStreetMap as its terms ask', () => {
  expect(nominatimSearch(answer([])).attribution).toBe('© OpenStreetMap contributors');
});

it('keeps at least a second between requests, as Nominatim asks', async () => {
  let time = 10_000;
  const sent: number[] = [];
  const slept: number[] = [];
  const clock: Clock = {
    now: () => time,
    sleep: async (ms) => {
      slept.push(ms);
      time += ms;
    },
  };
  const request = jest.fn(async () => {
    sent.push(time);
    time += 200; // the answer takes 200 ms
    return { ok: true, status: 200, json: async () => [] } as unknown as Response;
  });
  const search = nominatimSearch(request, clock);

  await search.search('Gruž', 'hr');
  await search.search('Gruž 1', 'hr');
  time += 5_000;
  await search.search('Lapad', 'hr');

  expect(sent).toEqual([10_000, 11_000, 16_200]);
  expect(slept).toEqual([MIN_REQUEST_INTERVAL_MS - 200]);
});

it('queues taps that come at the same time instead of sending them together', async () => {
  let time = 0;
  const sent: number[] = [];
  const clock: Clock = {
    now: () => time,
    sleep: async (ms) => {
      time += ms;
    },
  };
  const request = jest.fn(async () => {
    sent.push(time);
    return { ok: true, status: 200, json: async () => [] } as unknown as Response;
  });
  const search = nominatimSearch(request, clock);

  await Promise.all([search.search('Gruž', 'hr'), search.search('Lapad', 'hr')]);

  expect(sent).toEqual([0, MIN_REQUEST_INTERVAL_MS]);
});
