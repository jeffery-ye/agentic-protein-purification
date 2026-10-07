import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ManualReviewList from './ManualReviewList.svelte';
import type { BlastResult, Paper } from '../../lib/types/api';

function paper(pmid: string, access: Paper['access'], extra: Partial<Paper> = {}): Paper {
    const forReview = access === 'pmc_restricted' || access === 'not_in_pmc';
    return {
        pmid,
        access,
        for_manual_review: forReview,
        link: `https://pubmed.ncbi.nlm.nih.gov/${pmid}`,
        ...extra
    };
}

function hit(pdb_id: string, pmid: string, pident: number): BlastResult {
    return {
        protein_name: pdb_id,
        pdb_id,
        pmid,
        pident,
        length: 100,
        e_value: 0,
        query_coverage: 95,
        query_start: 1,
        query_end: 100,
        subject_start: 1,
        subject_end: 100,
        organism_name: 'Mycobacterium smegmatis'
    };
}

const PAPERS = [
    paper('1', 'open', { title: 'An open paper' }),
    paper('2', 'not_in_pmc', { title: 'A PubMed-only paper', journal: 'J. Test', year: 2020 }),
    paper('3', 'pmc_restricted', { title: 'A restricted paper', abstract: 'What they found.' })
];
// Similarity order: the restricted paper's hit ranks above the PubMed-only one.
const HITS = [hit('1ABC', '1', 95), hit('3GHI', '3', 90), hit('2DEF', '2', 80)];

describe('ManualReviewList', () => {
    it('lists only closed-access papers, in the order of the hits that cite them', () => {
        render(ManualReviewList, { papers: PAPERS, hits: HITS });

        const titles = screen.getAllByRole('link').map(link => link.textContent?.trim());
        expect(titles).toEqual(['A restricted paper', 'A PubMed-only paper']);
        expect(screen.getByText(/From hit 3GHI: 90.0% identity/)).toBeTruthy();
        expect(screen.getByText(/In PMC, not open access/)).toBeTruthy();
        expect(screen.getByText(/J\. Test, 2020/)).toBeTruthy();
    });

    it('puts the abstract behind a toggle on screen and in full in the PDF', () => {
        const { unmount } = render(ManualReviewList, { papers: PAPERS, hits: HITS });
        expect(screen.getByText('Abstract').tagName).toBe('SUMMARY');
        unmount();

        render(ManualReviewList, { papers: PAPERS, hits: HITS, print: true });
        expect(screen.queryByText('Abstract')).toBeNull();
        expect(screen.getByText('What they found.')).toBeTruthy();
    });

    it('says so when no paper is closed access', () => {
        render(ManualReviewList, { papers: [PAPERS[0]], hits: HITS });

        expect(screen.getByText('No closed-access papers among the hits.')).toBeTruthy();
    });
});
