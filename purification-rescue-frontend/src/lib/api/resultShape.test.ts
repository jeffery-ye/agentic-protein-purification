import { describe, expect, it } from 'vitest';
import { isRenderableResult } from './resultShape';

const PAPER = { pmid: '12345', title: 'A kinase', for_manual_review: true, link: 'https://pubmed.ncbi.nlm.nih.gov/12345' };

describe('isRenderableResult', () => {
    it('accepts a report with or without papers', () => {
        expect(isRenderableResult({ comprehensive_protocol: 'x', papers: [PAPER] })).toBe(true);
        expect(isRenderableResult({ comprehensive_protocol: 'x', papers: null })).toBe(true);
        expect(isRenderableResult({ comprehensive_protocol: 'x' })).toBe(true);
    });

    it.each([
        ['not a list', { pmid: '1' }],
        ['a paper that is not an object', ['12345']],
        ['a paper without a PMID', [{ title: 'No PMID' }]],
        ['a numeric PMID', [{ pmid: 12345 }]],
        ['a null paper', [null]]
    ])('rejects papers that are %s', (_, papers) => {
        expect(isRenderableResult({ comprehensive_protocol: 'x', papers })).toBe(false);
    });
});
