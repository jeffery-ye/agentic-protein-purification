import type { ProtocolResult } from '../types/api';

// Stored results are the report JSON as it was when the job ran, and later
// shape changes don't migrate them. These checks cover exactly what
// the report components dereference, so a result that passes renders without
// throwing. Anything else is shown as an outdated report instead.

type Obj = Record<string, unknown>;

function isObject(value: unknown): value is Obj {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isOptional(value: unknown, type: 'string' | 'number'): boolean {
    return value === undefined || value === null || typeof value === type;
}

function isOptionalArrayOf(value: unknown, check: (item: unknown) => boolean): boolean {
    return value === undefined || value === null || (Array.isArray(value) && value.every(check));
}

function isBufferStep(value: unknown): boolean {
    return isObject(value);
}

function isPurification(value: unknown): boolean {
    return (
        isObject(value) &&
        isOptional(value.article_link, 'string') &&
        isOptionalArrayOf(value.protocol, isBufferStep)
    );
}

function isBlastResult(value: unknown): boolean {
    return (
        isObject(value) &&
        typeof value.pident === 'number' &&
        typeof value.query_coverage === 'number' &&
        typeof value.e_value === 'number' &&
        isOptional(value.similarity_score, 'number') &&
        // The table falls back to the protein name when the organism is missing.
        (typeof value.organism_name === 'string' || typeof value.protein_name === 'string')
    );
}

// ManualReviewList keys and matches papers by PMID.
function isPaper(value: unknown): boolean {
    return isObject(value) && typeof value.pmid === 'string';
}

/** Whether a `/result` payload has the shape the current report can render. */
export function isRenderableResult(value: unknown): value is ProtocolResult {
    return (
        isObject(value) &&
        isOptional(value.comprehensive_protocol, 'string') &&
        isOptional(value.raw_plan, 'string') &&
        isOptional(value.error_message, 'string') &&
        isOptional(value.synthesis_skipped, 'string') &&
        isOptionalArrayOf(value.purifications, isPurification) &&
        isOptionalArrayOf(value.blast_results, isBlastResult) &&
        isOptionalArrayOf(value.papers, isPaper)
    );
}
