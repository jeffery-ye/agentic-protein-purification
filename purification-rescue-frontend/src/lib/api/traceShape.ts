import type { Trace } from '../types/api';

// A stored trace is the JSON as it was when the job ran (#77). These checks
// cover what the Job Info tab dereferences, so one that passes renders; anything
// else is shown as "no trace" rather than breaking the report.

type Obj = Record<string, unknown>;

function isObject(value: unknown): value is Obj {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isOptional(value: unknown, type: 'string' | 'number'): boolean {
    return value === undefined || value === null || typeof value === type;
}

function isStep(value: unknown): boolean {
    return (
        isObject(value) &&
        typeof value.agent === 'string' &&
        typeof value.seconds === 'number' &&
        isOptional(value.subject, 'string') &&
        isOptional(value.source_key, 'string') &&
        isOptional(value.reasoning, 'string') &&
        isOptional(value.output, 'string') &&
        isOptional(value.cost_usd, 'number')
    );
}

function isStage(value: unknown): boolean {
    return isObject(value) && typeof value.name === 'string' && typeof value.seconds === 'number';
}

/** Whether a `/trace` payload has the shape the Job Info tab can render. */
export function isRenderableTrace(value: unknown): value is Trace {
    return (
        isObject(value) &&
        Array.isArray(value.steps) &&
        value.steps.every(isStep) &&
        Array.isArray(value.stages) &&
        value.stages.every(isStage) &&
        typeof value.total_seconds === 'number' &&
        isOptional(value.cost_usd, 'number')
    );
}
