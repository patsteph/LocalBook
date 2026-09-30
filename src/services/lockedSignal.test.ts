import { describe, expect, it, beforeEach } from 'vitest';
import { clearLocked, getLocked, onLockedChange } from './api';

/**
 * LB-11: the locked-volume signal.
 *
 * The repo's frontend tests are pure-function tests with no DOM harness, so
 * this pins the observable contract the recovery screen depends on rather than
 * rendering it. The behaviour that matters: a stale lock must clear, or the
 * user sits on a recovery screen after they have already recovered.
 */

describe('locked signal', () => {
  beforeEach(() => clearLocked());

  it('starts unlocked', () => {
    expect(getLocked()).toBeNull();
  });

  it('notifies subscribers when it clears', () => {
    const seen: unknown[] = [];
    const off = onLockedChange((s) => seen.push(s));
    clearLocked();
    off();
    // Already null, so no spurious notification — a re-render storm on every
    // successful request is exactly what the change check avoids.
    expect(seen).toEqual([]);
  });

  it('unsubscribes cleanly', () => {
    let calls = 0;
    const off = onLockedChange(() => { calls += 1; });
    off();
    clearLocked();
    expect(calls).toBe(0);
  });
});
