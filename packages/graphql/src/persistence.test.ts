import { buildSchema, isInputObjectType, isInterfaceType, isObjectType } from 'graphql';
import { describe, expect, it } from 'vitest';
import sdl from '../schema.graphql?raw';
import { NOT_PERSISTED, PERSIST_CHECKED, withoutSensitiveData } from './persistence';

const strip = (data: object) => JSON.parse(withoutSensitiveData(JSON.stringify(data))) as unknown;

describe('withoutSensitiveData', () => {
  it('removes denied fields, keeping the rest of the object', () => {
    expect(
      strip({
        'Task:t1': {
          __typename: 'Task',
          id: 't1',
          title: 'Groceries',
          approximateLat: 42.64,
          address: 'Ul. od Puča 7',
          lat: 42.6407,
          lng: 18.1077,
          accessDetails: 'Gate code 4512',
        },
        'CareContact:c1': { __typename: 'CareContact', id: 'c1', name: 'Kata', phone: '+385' },
      }),
    ).toEqual({
      'Task:t1': { __typename: 'Task', id: 't1', title: 'Groceries', approximateLat: 42.64 },
      'CareContact:c1': { __typename: 'CareContact', id: 'c1', name: 'Kata' },
    });
  });

  it("keeps only the language and ID of a booked task's on-site contact", () => {
    expect(
      strip({
        'OnSiteContact:c1': {
          __typename: 'OnSiteContact',
          id: 'c1',
          name: 'Kata',
          area: 'Gruž, Dubrovnik',
          language: 'HR',
          phone: '+385912345678',
        },
      }),
    ).toEqual({ 'OnSiteContact:c1': { __typename: 'OnSiteContact', id: 'c1', language: 'HR' } });
  });

  it('drops credential objects, every field pointing at them, and ROOT_MUTATION', () => {
    expect(
      strip({
        ROOT_QUERY: {
          __typename: 'Query',
          'tasks({"first":10})': [{ __ref: 'Task:t1' }],
          lastSession: { __ref: 'AuthSession:s1' },
          sessions: [{ __ref: 'AuthSession:s1' }],
        },
        ROOT_MUTATION: { __typename: 'Mutation', webSocketToken: 'secret' },
        'Task:t1': { __typename: 'Task', id: 't1', title: 'Groceries' },
        'AuthSession:s1': { __typename: 'AuthSession', id: 's1', accessToken: 'secret' },
      }),
    ).toEqual({
      ROOT_QUERY: { __typename: 'Query', 'tasks({"first":10})': [{ __ref: 'Task:t1' }] },
      'Task:t1': { __typename: 'Task', id: 't1', title: 'Groceries' },
    });
  });

  it('matches fields stored with arguments, and strips embedded objects by their type', () => {
    expect(
      strip({
        'Task:t1': {
          __typename: 'Task',
          id: 't1',
          'address({"format":"long"})': 'Ul. od Puča 7',
          contact: { __typename: 'CareContact', name: 'Kata', phone: '+385' },
          link: { __typename: 'UploadTarget', url: 'https://r2.example/put' },
        },
      }),
    ).toEqual({
      'Task:t1': {
        __typename: 'Task',
        id: 't1',
        contact: { __typename: 'CareContact', name: 'Kata' },
      },
    });
  });
});

/**
 * Every output field whose name looks like an address, location, access detail, phone,
 * message text or credential is on the deny-list or checked as safe. A new one (chat
 * messages, say) fails here until someone decides which.
 */
describe('the deny-list covers schema.graphql', () => {
  const SENSITIVE_NAME =
    /address|street|postal|phone|access|doorbell|floor|gate|^lat$|^lng$|message|body|text|token|secret|nonce|ticket|code|key|password|signature|^url$/i;
  const schema = buildSchema(sdl);
  const outputFields = Object.values(schema.getTypeMap())
    .filter((type) => (isObjectType(type) || isInterfaceType(type)) && !type.name.startsWith('__'))
    .filter((type) => !isInputObjectType(type) && type.name !== 'Mutation')
    .flatMap((type) =>
      Object.keys((type as { getFields(): object }).getFields()).map((field) => ({
        type: type.name,
        field,
      })),
    );

  it('classifies every sensitive-looking field', () => {
    const unclassified = outputFields
      .filter(({ type, field }) => {
        if (!SENSITIVE_NAME.test(field)) return false;
        const denied = NOT_PERSISTED[type];
        return (
          denied !== 'all' &&
          !denied?.includes(field) &&
          !PERSIST_CHECKED.includes(`${type}.${field}`)
        );
      })
      .map(({ type, field }) => `${type}.${field}`);
    expect(unclassified).toEqual([]);
  });

  it('names only types and fields that exist', () => {
    const paths = new Set(outputFields.map(({ type, field }) => `${type}.${field}`));
    const types = new Set(outputFields.map(({ type }) => type));
    for (const [type, denied] of Object.entries(NOT_PERSISTED)) {
      expect(types).toContain(type);
      if (denied !== 'all') for (const field of denied) expect(paths).toContain(`${type}.${field}`);
    }
    for (const path of PERSIST_CHECKED) expect(paths).toContain(path);
  });
});
