import type { ApolloClient } from '@apollo/client';
import { renderHook } from '@testing-library/react-native';
import { AppState, type AppStateStatus } from 'react-native';
import { useRefetchOnForeground } from '../src/apollo/foreground';

async function setup() {
  let listener: (state: AppStateStatus) => void = () => {};
  const remove = jest.fn();
  jest.spyOn(AppState, 'addEventListener').mockImplementation((_type, handler) => {
    listener = handler as (state: AppStateStatus) => void;
    return { remove };
  });
  const refetchQueries = jest.fn().mockResolvedValue([]);
  const client = { refetchQueries } as unknown as ApolloClient;
  const hook = await renderHook(() => useRefetchOnForeground(client));
  // The listener sets no React state, so it needs no act().
  const change = (state: AppStateStatus) => listener(state);
  return { change, refetchQueries, remove, hook };
}

afterEach(() => jest.restoreAllMocks());

describe('useRefetchOnForeground', () => {
  it('refetches the active queries after returning from the background', async () => {
    const { change, refetchQueries } = await setup();

    change('inactive');
    change('background');
    expect(refetchQueries).not.toHaveBeenCalled();

    change('active');
    expect(refetchQueries).toHaveBeenCalledTimes(1);
    expect(refetchQueries).toHaveBeenCalledWith({ include: 'active' });
  });

  it('ignores an inactive blip that never reached the background', async () => {
    const { change, refetchQueries } = await setup();

    change('inactive');
    change('active');

    expect(refetchQueries).not.toHaveBeenCalled();
  });

  it('refetches once per return to the foreground', async () => {
    const { change, refetchQueries } = await setup();

    change('background');
    change('active');
    change('active');
    change('background');
    change('active');

    expect(refetchQueries).toHaveBeenCalledTimes(2);
  });

  it('stops listening on unmount', async () => {
    const { hook, remove } = await setup();

    await hook.unmount();

    expect(remove).toHaveBeenCalled();
  });
});
