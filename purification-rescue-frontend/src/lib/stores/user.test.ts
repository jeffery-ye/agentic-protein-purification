import { describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { api, signIn } from '../api/client';
import { signedIn, userStore } from './user';
import type { CurrentUser } from '../types/api';

const USER: CurrentUser = {
    sub: 'abc',
    username: 'reviewer',
    email: 'owner@example.org',
    auth_enabled: true,
    can_change_password: false
};

describe('userStore.load', () => {
    it('holds the signed-in user', async () => {
        vi.spyOn(api, 'me').mockResolvedValue(USER);

        await userStore.load();

        expect(get(userStore)).toEqual({ status: 'ready', user: USER });
        expect(get(signedIn)).toBe(true);
    });

    it('is ready with no user for a signed-out visitor, who can browse the home page', async () => {
        vi.spyOn(api, 'me').mockResolvedValue(null);

        await userStore.load();

        expect(get(userStore)).toEqual({ status: 'ready', user: null });
        expect(get(signedIn)).toBe(false);
    });

    it('lets the app render without a user when /auth/me fails otherwise', async () => {
        vi.spyOn(api, 'me').mockRejectedValue(new Error('Failed to fetch'));
        vi.spyOn(console, 'error').mockImplementation(() => {});

        await userStore.load();

        expect(get(userStore)).toEqual({ status: 'error', user: null });
    });
});

describe('userStore.requireSignIn', () => {
    it('lets a signed-in user through', async () => {
        vi.spyOn(api, 'me').mockResolvedValue(USER);
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});
        await userStore.load();

        expect(userStore.requireSignIn()).toBe(true);
        expect(redirect).not.toHaveBeenCalled();
    });

    it('sends a signed-out visitor to sign in and blocks the route', async () => {
        vi.spyOn(api, 'me').mockResolvedValue(null);
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});
        await userStore.load();

        expect(userStore.requireSignIn()).toBe(false);
        expect(redirect).toHaveBeenCalledWith(expect.stringMatching(/\/auth\/login$/));
    });

    it('comes back to the page after sign-in', async () => {
        vi.spyOn(api, 'me').mockResolvedValue(null);
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});
        await userStore.load();
        window.location.hash = '#/report/abc-123';

        try {
            expect(userStore.requireSignIn()).toBe(false);
            expect(redirect).toHaveBeenCalledWith(
                expect.stringMatching(/\/auth\/login\?next=%2F%23%2Freport%2Fabc-123$/)
            );
        } finally {
            window.location.hash = '';
        }
    });

    it('blocks the route without sending to sign in when /auth/me failed', async () => {
        vi.spyOn(api, 'me').mockRejectedValue(new Error('API Error: Bad Gateway'));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});
        await userStore.load();

        expect(userStore.requireSignIn()).toBe(false);
        expect(redirect).not.toHaveBeenCalled();
    });

    it('lets the user through once a retry reaches the server', async () => {
        const me = vi.spyOn(api, 'me').mockRejectedValue(new Error('Failed to fetch'));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        await userStore.load();
        me.mockResolvedValue(USER);

        await userStore.load();

        expect(get(userStore)).toEqual({ status: 'ready', user: USER });
        expect(userStore.requireSignIn()).toBe(true);
    });
});
