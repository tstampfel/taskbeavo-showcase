import { NOMINATIM_USER_AGENT, nominatimSearch } from '../src/tasks/address-search';

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
