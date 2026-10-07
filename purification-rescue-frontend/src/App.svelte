<script lang="ts">
  import { onMount, type Component } from "svelte";
  import Router from "svelte-spa-router";
  import { wrap } from "svelte-spa-router/wrap";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import Home from "./routes/Home.svelte";
  import Processing from "./routes/Processing.svelte";
  import Report from "./routes/Report.svelte";
  import MyJobs from "./routes/MyJobs.svelte";
  import Profile from "./routes/Profile.svelte";
  import Layout from "./components/common/Layout.svelte";
  import { userStore } from "./lib/stores/user";
  import { jobsStore } from "./lib/stores/jobs";

  // Signed-out visitors can browse the home page. Pages that show a user's
  // own data send them to sign in instead.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const privateRoute = (component: Component<any>) =>
    wrap({ component, conditions: [() => userStore.requireSignIn()] });

  const routes = {
    "/": Home,
    "/jobs": privateRoute(MyJobs),
    "/profile": privateRoute(Profile),
    "/processing/:jobId": privateRoute(Processing),
    "/report/:jobId": privateRoute(Report)
  };

  let stopRefresh: (() => void) | undefined;

  // Who is signed in comes first, so the route guards can decide. Then, for a
  // signed-in user, the job list keeps refreshing for My Jobs and the
  // running-jobs indicator. A retry after a failed /auth/me comes through here
  // too; the router remounts, so the page's guard runs again.
  async function loadUser() {
    await userStore.load();
    if ($userStore.user && !stopRefresh) stopRefresh = jobsStore.startAutoRefresh();
  }

  onMount(() => {
    void loadUser();
    return () => stopRefresh?.();
  });
</script>

<Layout>
  {#if $userStore.status === "error"}
    <Alert.Root class="mb-4 border-yellow-300 bg-yellow-50 text-yellow-900">
      <Alert.Title class="font-bold">Could not reach the server</Alert.Title>
      <Alert.Description class="text-yellow-900">
        <p>We couldn't check whether you're signed in. Your jobs are safe; try again in a moment.</p>
        <Button variant="outline" size="sm" class="mt-2" onclick={() => void loadUser()}>Retry</Button>
      </Alert.Description>
    </Alert.Root>
  {/if}
  {#if $userStore.status !== "loading"}
    <Router {routes} />
  {/if}
</Layout>
