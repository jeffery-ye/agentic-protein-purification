import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/svelte';
import ProtocolViewer from './ProtocolViewer.svelte';

describe('ProtocolViewer', () => {
    it('renders the protocol Markdown', () => {
        const { container } = render(ProtocolViewer, {
            comprehensive_protocol: '## Lysis\n\n- 50 mM Tris, [source](https://example.org/paper)'
        });

        expect(container.querySelector('h2')?.textContent).toBe('Lysis');
        expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.org/paper');
    });

    it('never renders an image, however the protocol writes one (#98)', () => {
        const { container } = render(ProtocolViewer, {
            comprehensive_protocol: [
                '![pixel](https://tracker.example/p.gif)',
                '<img src="https://tracker.example/a.gif">',
                '<picture><source srcset="https://tracker.example/b.gif"></picture>',
                '<video poster="https://tracker.example/c.gif"></video>',
                '<svg><image href="https://tracker.example/d.gif"/></svg>',
                '<p style="background:url(https://tracker.example/e.gif)">Buffer A</p>'
            ].join('\n\n')
        });

        expect(container.querySelector('img, picture, source, video, svg, image')).toBeNull();
        expect(container.innerHTML).not.toContain('tracker.example');
        expect(container.textContent).toContain('Buffer A');
    });
});
