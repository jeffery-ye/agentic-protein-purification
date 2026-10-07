import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ResultDashboard from './ResultDashboard.svelte';
import type { ProtocolResult } from '../../lib/types/api';

const REASON =
    'No source protocols were extracted from the literature, so there was nothing to compare the target against.';

const BASE: ProtocolResult = {
    blast_results: [
        {
            protein_name: 'pdb|1ABC|A',
            pdb_id: '1ABC',
            organism_name: 'Mycobacterium tuberculosis',
            pident: 92,
            query_coverage: 95,
            e_value: 1e-50,
            length: 350,
            query_start: 1,
            query_end: 100,
            subject_start: 1,
            subject_end: 100,
            similarity_score: 0.92
        }
    ],
    purifications: []
};

describe('a skipped synthesis (#89)', () => {
    it('explains the skip in both the screen and print trees', () => {
        render(ResultDashboard, { result: { ...BASE, synthesis_skipped: REASON } });

        expect(screen.getAllByText('No protocol synthesized')).toHaveLength(2);
        expect(screen.getAllByText(REASON)).toHaveLength(2);
    });

    it('does not present the skip as a failure', () => {
        render(ResultDashboard, { result: { ...BASE, synthesis_skipped: REASON } });

        expect(screen.queryByText('Protocol synthesis failed')).toBeNull();
        // The expensive part of the run is unaffected by the skip.
        expect(screen.getByRole('button', { name: 'BLAST Analysis' })).toBeTruthy();
    });

    it('shows nothing for a report from before the field existed', () => {
        render(ResultDashboard, { result: BASE });

        expect(screen.queryByText('No protocol synthesized')).toBeNull();
    });

    it('still reports a real synthesis failure as a failure', () => {
        render(ResultDashboard, {
            result: { ...BASE, error_message: 'ModelHTTPError: status_code: 400' }
        });

        expect(screen.getAllByText('Protocol synthesis failed')).toHaveLength(2);
        expect(screen.queryByText('No protocol synthesized')).toBeNull();
    });
});
