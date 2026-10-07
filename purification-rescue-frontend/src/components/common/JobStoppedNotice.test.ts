import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import JobStoppedNotice from './JobStoppedNotice.svelte';

describe('JobStoppedNotice', () => {
    it('shows a failed job with its error and a way to try again', () => {
        render(JobStoppedNotice, { state: 'failed', error: 'No BLAST results found' });

        expect(screen.getByText('Analysis failed')).toBeTruthy();
        expect(screen.getByText('No BLAST results found')).toBeTruthy();
        expect(screen.getByRole('link', { name: 'Try again' })).toBeTruthy();
    });

    it('tells the user an interrupted job was not their fault', () => {
        render(JobStoppedNotice, { state: 'interrupted' });

        expect(screen.getByText('Analysis interrupted')).toBeTruthy();
        expect(screen.getByText(/Nothing is wrong with your input/)).toBeTruthy();
    });
});
