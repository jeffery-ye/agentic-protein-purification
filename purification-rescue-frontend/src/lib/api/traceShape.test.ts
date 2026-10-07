import { describe, expect, it } from 'vitest';
import { isRenderableTrace } from './traceShape';

const TRACE = {
    model: 'openai:gpt-6-luna',
    steps: [{ agent: 'extraction', seconds: 3.2, subject: 'PDB 1ABC', source_key: '1ABC', reasoning: null }],
    stages: [{ name: 'Synthesis', seconds: 12.5 }],
    total_seconds: 12.5,
    input_tokens: 100,
    output_tokens: 20,
    reasoning_tokens: 5,
    cost_usd: null
};

describe('isRenderableTrace', () => {
    it('accepts a trace the tab can render, with or without a cost', () => {
        expect(isRenderableTrace(TRACE)).toBe(true);
        expect(isRenderableTrace({ ...TRACE, cost_usd: 0.0123 })).toBe(true);
    });

    it('rejects null and shapes the tab would trip over', () => {
        expect(isRenderableTrace(null)).toBe(false);
        expect(isRenderableTrace({ ...TRACE, steps: [{ agent: 'extraction' }] })).toBe(false);
        expect(isRenderableTrace({ ...TRACE, stages: 'fast' })).toBe(false);
        expect(isRenderableTrace({ ...TRACE, total_seconds: undefined })).toBe(false);
    });
});
