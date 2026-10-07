import { derived, get, writable } from 'svelte/store';
import { api, signIn, signInHere } from '../api/client';
import type { CurrentUser } from '../types/api';

// --- User Store ---

export interface UserView {
    /**
     * loading until /auth/me answers. ready with user null means nobody is
     * signed in; error means /auth/me failed for another reason.
     */
    status: 'loading' | 'ready' | 'error';
    user: CurrentUser | null;
}

function createUserStore() {
    const store = writable<UserView>({ status: 'loading', user: null });
    const { subscribe, set } = store;

    return {
        subscribe,
        /**
         * Loads the signed-in user at startup, and again on a retry. Signed-out
         * visitors get user null and can browse the home page; any other
         * failure (the backend is down, say) is an error, and the app still
         * renders without a user.
         */
        load: async () => {
            set({ status: 'loading', user: null });
            try {
                set({ status: 'ready', user: await api.me() });
            } catch (err) {
                console.error('Could not load the signed-in user:', err);
                set({ status: 'error', user: null });
            }
        },

        /**
         * A route guard for pages that show a user's own data. Lets the route
         * through when someone is signed in, and sends a signed-out visitor to
         * sign in, coming back to this page. When /auth/me failed (a deploy
         * restart, say) nobody is known to be signed out, so it only blocks
         * the route and the app offers a retry.
         */
        requireSignIn: (): boolean => {
            const { status, user } = get(store);
            if (user) return true;
            if (status === 'ready') signIn.redirect(signInHere());
            return false;
        }
    };
}

export const userStore = createUserStore();

/** Whether someone is signed in. */
export const signedIn = derived(userStore, view => view.user !== null);
