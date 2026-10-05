import { describe, it, expect } from 'vitest';
import { conflictLabel, conflictValue } from './conflictLabel';

describe('sync conflict labels', () => {
    const mem = {
        tbl: 'documents', pk: '["core_memory","e1"]', field: 'body_json',
        kept_value: JSON.stringify({ key: 'home city', value: 'Lisbon' }),
        other_value: JSON.stringify({ key: 'home city', value: 'Porto' }),
    };

    it('names a core memory by its key and shows its value', () => {
        expect(conflictLabel(mem)).toBe('Core memory: home city');
        expect(conflictValue(mem, mem.kept_value)).toBe('Lisbon');
        expect(conflictValue(mem, mem.other_value)).toBe('Porto');
    });

    it('names per-notebook settings and pretty-prints them', () => {
        const c = { tbl: 'documents', pk: '["collector_config","1d9f4771-8dcf"]', field: 'body_json',
            kept_value: JSON.stringify({ intent: 'a' }), other_value: JSON.stringify({ intent: 'b' }) };
        expect(conflictLabel(c)).toBe('Collector settings (1d9f4771…)');
        expect(conflictValue(c, c.kept_value)).toContain('"intent": "a"');
    });

    it('keeps plain tables readable', () => {
        const c = { tbl: 'canvas_notes', field: 'content_markdown', kept_value: 'one', other_value: 'two' };
        expect(conflictLabel(c)).toBe('Note · content_markdown');
        expect(conflictValue(c, null)).toBe('(empty)');
    });
});
