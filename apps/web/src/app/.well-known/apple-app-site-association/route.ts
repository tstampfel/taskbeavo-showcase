import { appleAppSiteAssociation, associationResponse } from '../../../app-links/association';

/** iOS universal links. Served without a file extension, as application/json. */
export function GET(): Response {
  return associationResponse(appleAppSiteAssociation(process.env));
}
