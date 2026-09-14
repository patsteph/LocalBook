/**
 * Journey Canvas transforms — the first frontend tests in this repo.
 *
 * Context: the backend has ~400 tests; the frontend had none, so every canvas behaviour was
 * verified by `tsc --noEmit` plus eyeballs. Splitting the pure layout math out of
 * JourneyCanvas.tsx (2026-08-18) is what made it assertable — these functions take plain data
 * and return plain data, no react-flow runtime, no DOM.
 *
 * Deliberately scoped to the PURE layer. Rendering the canvas would need jsdom +
 * @testing-library + a react-flow harness; that's a bigger call than a refactor should smuggle in.
 */
import { describe, it, expect } from 'vitest';

import {
  computeRanks,
  computeComposition,
  toFlowNode,
  toFlowEdge,
  fromFlowNode,
  toCandidateRef,
  recencyOpacity,
  pairKey,
} from './journeyTransforms';
import type { CanvasNode, CanvasEdge } from '../../services/canvas';
import type { CanvasFlowNode } from './journeyNodeTypes';

function node(over: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id: 'n1', x: 0, y: 0, kind: 'artifact', ref_type: 'exploration_query', ref_id: 'q1',
    title: 'A question', snapshot: { id: 's', type: 'markdown', payload: 'body' },
    z: 0, created_at: '2026-08-01T10:00:00', topic_id: null, parent_id: null,
    ...over,
  } as CanvasNode;
}

describe('computeRanks — the journey\'s direction of travel', () => {
  it('ranks a card\'s children in READING order (y then x), not array order', () => {
    // Deliberately shuffled: the array says c,a,b — position says a,b,c.
    const nodes = [
      node({ id: 'c', parent_id: 'card', y: 100, x: 0 }),
      node({ id: 'a', parent_id: 'card', y: 0, x: 0 }),
      node({ id: 'b', parent_id: 'card', y: 0, x: 200 }),
    ];
    const ranks = computeRanks(nodes);
    expect(ranks.get('a')).toEqual({ index: 1, total: 3 });
    expect(ranks.get('b')).toEqual({ index: 2, total: 3 });
    expect(ranks.get('c')).toEqual({ index: 3, total: 3 });
  });

  it('ranks each card independently', () => {
    const nodes = [
      node({ id: 'a1', parent_id: 'cardA', y: 0 }), node({ id: 'a2', parent_id: 'cardA', y: 10 }),
      node({ id: 'b1', parent_id: 'cardB', y: 0 }), node({ id: 'b2', parent_id: 'cardB', y: 10 }),
    ];
    const ranks = computeRanks(nodes);
    expect(ranks.get('a1')).toEqual({ index: 1, total: 2 });
    expect(ranks.get('b1')).toEqual({ index: 1, total: 2 });
  });

  it('gives a lone child NO number — "1/1" is noise, not a sequence', () => {
    expect(computeRanks([node({ id: 'only', parent_id: 'card' })]).size).toBe(0);
  });

  it('ignores orphans — a thread outside any card is not part of a sequence', () => {
    expect(computeRanks([node({ id: 'o1' }), node({ id: 'o2' })]).size).toBe(0);
  });
});

describe('computeComposition — what a collapsed topic card holds', () => {
  it('counts child ref_types, biggest group first', () => {
    const nodes = [
      node({ id: '1', parent_id: 'card', ref_type: 'exploration_query' }),
      node({ id: '2', parent_id: 'card', ref_type: 'exploration_query' }),
      node({ id: '3', parent_id: 'card', ref_type: 'audio' }),
      node({ id: '4', parent_id: 'card', ref_type: 'document' }),
      node({ id: '5', parent_id: 'card', ref_type: 'document' }),
      node({ id: '6', parent_id: 'card', ref_type: 'document' }),
    ];
    expect(computeComposition(nodes).get('card')).toEqual([
      { refType: 'document', count: 3 },
      { refType: 'exploration_query', count: 2 },
      { refType: 'audio', count: 1 },
    ]);
  });

  it('breaks count ties alphabetically so the icon row is stable across renders', () => {
    const nodes = [
      node({ id: '1', parent_id: 'card', ref_type: 'video' }),
      node({ id: '2', parent_id: 'card', ref_type: 'audio' }),
    ];
    expect(computeComposition(nodes).get('card')?.map((c) => c.refType)).toEqual(['audio', 'video']);
  });

  it('ignores nodes with no parent', () => {
    expect(computeComposition([node({ id: 'x' })]).size).toBe(0);
  });
});

describe('toFlowNode', () => {
  it('makes a child reference its card and clamps it to the card', () => {
    const fn = toFlowNode(node({ id: 'kid', parent_id: 'card' }));
    expect(fn.parentId).toBe('card');
    expect(fn.extent).toBe('parent');
  });

  it('leaves a top-level node unparented', () => {
    const fn = toFlowNode(node({ id: 'solo' }));
    expect(fn.parentId).toBeUndefined();
    expect(fn.extent).toBeUndefined();
  });

  it('marks a thread with no card AND no topic as an orphan', () => {
    const fn = toFlowNode(node({ id: 'o', topic_id: null, parent_id: null }));
    expect((fn.data as { isOrphan?: boolean }).isOrphan).toBe(true);
    // ...but a thread that HAS a topic is not an orphan even before it is placed in a card.
    const assigned = toFlowNode(node({ id: 'a', topic_id: 't1', parent_id: null }));
    expect((assigned.data as { isOrphan?: boolean }).isOrphan).toBe(false);
  });

  it('routes a topic node to the group renderer, never the chip renderer', () => {
    expect(toFlowNode(node({ id: 'c', kind: 'topic', ref_type: 'topic' })).type).toBe('topicCard');
    expect(toFlowNode(node({ id: 'n' })).type).toBe('artifact');
  });

  it('carries rank / open-loop / composition extras onto the node data', () => {
    const chip = toFlowNode(node({ id: 'n' }), { rank: { index: 2, total: 5 }, openLoop: 'why' });
    expect((chip.data as { rank?: unknown }).rank).toEqual({ index: 2, total: 5 });
    expect((chip.data as { openLoop?: string }).openLoop).toBe('why');

    const card = toFlowNode(node({ id: 'c', kind: 'topic' }), {
      composition: [{ refType: 'audio', count: 1 }],
    });
    expect((card.data as { composition?: unknown }).composition).toEqual([{ refType: 'audio', count: 1 }]);
  });
});

describe('toFlowEdge — the five-state visual language', () => {
  const edge = (over: Partial<CanvasEdge> = {}): CanvasEdge => ({
    id: 'e1', source: 'a', target: 'b', state: 'user', label: '', meta: {},
    created_at: '2026-08-01T10:00:00', ...over,
  } as CanvasEdge);

  it('arrows only the DIRECTED states (made-from and researched)', () => {
    expect(toFlowEdge(edge({ state: 'provenance' })).markerEnd).toBeTruthy();
    expect(toFlowEdge(edge({ state: 'researched' })).markerEnd).toBeTruthy();
    expect(toFlowEdge(edge({ state: 'user' })).markerEnd).toBeUndefined();
    expect(toFlowEdge(edge({ state: 'candidate' })).markerEnd).toBeUndefined();
  });

  it('falls back to the user style for an unknown state rather than rendering nothing', () => {
    const e = toFlowEdge(edge({ state: 'not-a-state' as CanvasEdge['state'] }));
    expect(e.style?.stroke).toBeTruthy();
  });

  it('prefers a researched edge\'s insight over its label', () => {
    expect(toFlowEdge(edge({ state: 'researched', label: 'plain', meta: { insight: 'the finding' } })).label)
      .toBe('the finding');
  });
});

describe('fromFlowNode — the round trip that gets PERSISTED', () => {
  it('keeps a topic card\'s BACKEND size, so collapsing never shrinks the stored layout', () => {
    const card = toFlowNode(node({ id: 'c', kind: 'topic', width: 656, height: 480 })) as CanvasFlowNode;
    // Simulate the collapse height-swap the renderer applies as pure view state.
    const collapsed = { ...card, height: 56 } as CanvasFlowNode;
    expect(fromFlowNode(collapsed).height).toBe(480);
  });

  it('DOES keep a thread\'s user resize', () => {
    const chip = toFlowNode(node({ id: 'n', width: 300, height: 180 })) as CanvasFlowNode;
    const resized = { ...chip, width: 420, height: 260 } as CanvasFlowNode;
    const out = fromFlowNode(resized);
    expect(out.width).toBe(420);
    expect(out.height).toBe(260);
  });

  it('preserves position and backend metadata', () => {
    const chip = toFlowNode(node({ id: 'n', ref_id: 'q7', title: 'T' })) as CanvasFlowNode;
    const moved = { ...chip, position: { x: 12, y: 34 } } as CanvasFlowNode;
    const out = fromFlowNode(moved);
    expect([out.x, out.y]).toEqual([12, 34]);
    expect(out.ref_id).toBe('q7');
    expect(out.title).toBe('T');
  });
});

describe('recencyOpacity', () => {
  it('never fades below the legibility floor, however old', () => {
    expect(recencyOpacity('2020-01-01T00:00:00')).toBe(0.4);
  });

  it('is fully opaque for something just created, and for an unknown/!bad date', () => {
    expect(recencyOpacity(new Date().toISOString())).toBeCloseTo(1, 1);
    expect(recencyOpacity(undefined)).toBe(1);
    expect(recencyOpacity('not-a-date')).toBe(1);
  });
});

describe('pairKey — direction-independent de-dup', () => {
  it('gives the same key whichever way round the pair is', () => {
    expect(pairKey('a', 'b')).toBe(pairKey('b', 'a'));
    expect(pairKey('a', 'b')).not.toBe(pairKey('a', 'c'));
  });
});

describe('toCandidateRef', () => {
  it('sends the snapshot text when it is a string, and empty otherwise', () => {
    expect(toCandidateRef(node({ snapshot: { id: 's', type: 'markdown', payload: 'hello' } })).text)
      .toBe('hello');
    // A structured payload (infographic/chart) has no text to embed — must not stringify an object.
    expect(toCandidateRef(node({ snapshot: { id: 's', type: 'json:infographic', payload: { a: 1 } } })).text)
      .toBe('');
  });
});
