import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TraceView from './TraceView.svelte';
import type { ProtocolResult, Trace } from '../../lib/types/api';

const getTrace = vi.fn();
vi.mock('../../lib/api/client', () => ({ api: { getTrace: (id: string) => getTrace(id) } }));

const RESULT: ProtocolResult = {
    raw_plan: 'Draft plan.',
    purifications: [
        { article_title: 'A paper', pdb_id: '1ABC', source: 'paper', purification_text: 'Ni-NTA, 250 mM imidazole.' }
    ]
};

const TRACE: Trace = {
    model: 'openai:gpt-6-luna',
    steps: [
        { agent: 'extraction', seconds: 3, subject: 'PDB 1ABC', source_key: '1ABC', reasoning: 'Found the Methods.', input_tokens: 1000, output_tokens: 50 },
        { agent: 'extraction', seconds: 2, subject: 'PDB 2DEF', source_key: '2DEF', reasoning: null },
        { agent: 'planner', seconds: 20, subject: 'Synthesis', reasoning: null }
    ],
    stages: [{ name: 'Synthesis', seconds: 25 }],
    total_seconds: 25,
    input_tokens: 1000,
    output_tokens: 50,
    reasoning_tokens: 0,
    cost_usd: 0.0021
};

describe('TraceView', () => {
    it('shows the summary, each source protocol with its steps, and other papers read', async () => {
        getTrace.mockResolvedValue(TRACE);
        render(TraceView, { jobId: 'job', result: RESULT });

        expect(await screen.findByText('openai:gpt-6-luna')).toBeTruthy();
        expect(screen.getByText('~$0.0021')).toBeTruthy();
        expect(screen.getByText('Ni-NTA, 250 mM imidazole.')).toBeTruthy();
        expect(screen.getByText('Found the Methods.')).toBeTruthy();
        expect(screen.getByText('Papers read without a protocol')).toBeTruthy();
        expect(screen.getByText('PDB 2DEF')).toBeTruthy();
        expect(screen.getByText('Draft plan.')).toBeTruthy();
    });

    it("shows a failed call's error", async () => {
        getTrace.mockResolvedValue({
            ...TRACE,
            steps: [{ agent: 'planner', seconds: 60, subject: 'Synthesis', error: 'ModelHTTPError: status 429' }]
        });
        render(TraceView, { jobId: 'job', result: RESULT });

        expect(await screen.findByText('This call failed: ModelHTTPError: status 429')).toBeTruthy();
    });

    it('shows an empty state for a job without a trace', async () => {
        getTrace.mockResolvedValue(null);
        render(TraceView, { jobId: 'old-job', result: RESULT });

        expect(await screen.findByText(/No job info for this job/)).toBeTruthy();
    });
});
