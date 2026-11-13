import { assetLinks, associationResponse } from '../../../app-links/association';

/** Android App Links verification. */
export function GET(): Response {
  return associationResponse(assetLinks(process.env));
}
