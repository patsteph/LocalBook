/**
 * Human labels for sync conflict items (LB-12). A conflict on a setting or memory used
 * to read "documents · body_json · concurrent-edit" over two blocks of raw JSON.
 */

const DOC_LABELS: Record<string, string> = {
    core_memory: 'Core memory',
    user_profile: 'Your profile',
    app_preferences: 'App preferences',
    curator_config: 'Curator settings',
    collector_config: 'Collector settings',
    people_config: 'People settings',
    quiz_card: 'Quiz card',
    quiz_review: 'Quiz review',
    approval_item: 'Collector approval item',
};

const TABLE_LABELS: Record<string, string> = {
    canvas_notes: 'Note', notebooks: 'Notebook', sources: 'Source', findings: 'Finding',
    highlights: 'Highlight', insights: 'Insight',
};

export type ConflictLike = { tbl: string; pk?: string; field: string; kept_value: unknown; other_value: unknown };

function parse(v: unknown): unknown {
    if (typeof v !== 'string') return v;
    try { return JSON.parse(v); } catch { return v; }
}

/** "Core memory: home city", "Collector settings (nb 1d9f…)", "Note · content_markdown". */
export function conflictLabel(c: ConflictLike): string {
    if (c.tbl === 'documents' && c.pk) {
        let kind = '', key = '';
        try { [kind, key] = JSON.parse(c.pk); } catch { /* fall through */ }
        const name = DOC_LABELS[kind] ?? kind ?? 'Setting';
        const body = (parse(c.kept_value) ?? parse(c.other_value)) as Record<string, unknown> | null;
        if (kind === 'core_memory' && body && typeof body === 'object' && body.key) return `${name}: ${String(body.key)}`;
        if (key && key !== 'main') return `${name} (${key.length > 12 ? key.slice(0, 8) + '…' : key})`;
        return name;
    }
    return `${TABLE_LABELS[c.tbl] ?? c.tbl} · ${c.field}`;
}

/** A value as people read it: settings/memory bodies pretty-printed, text as-is. */
export function conflictValue(c: ConflictLike, v: unknown): string {
    if (v === null || v === undefined) return '(empty)';
    const p = c.tbl === 'documents' ? parse(v) : v;
    if (typeof p === 'string') return p;
    if (c.tbl === 'documents' && p && typeof p === 'object' && 'value' in (p as object)) {
        return String((p as Record<string, unknown>).value);          // a core memory's value
    }
    return JSON.stringify(p, null, 2);
}
