import { describe, expect, it } from 'vitest';
import { geoliteAttributionOrder } from './geolite';

describe('geoliteAttributionOrder', () => {
  it('keeps the English original, with Croatian first and German and French after it', () => {
    expect(geoliteAttributionOrder('en')).toEqual(['english']);
    expect(geoliteAttributionOrder('hr')).toEqual(['local', 'english']);
    expect(geoliteAttributionOrder('de')).toEqual(['english', 'local']);
    expect(geoliteAttributionOrder('fr')).toEqual(['english', 'local']);
  });
});
