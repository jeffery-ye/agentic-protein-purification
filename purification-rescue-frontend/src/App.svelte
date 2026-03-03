<script lang="ts">
  import Router from "svelte-spa-router";
  import Home from "./routes/Home.svelte";
  import Processing from "./routes/Processing.svelte";
  import Report from "./routes/Report.svelte";
  import Layout from "./components/common/Layout.svelte";

  const routes = {
    "/": Home,
    "/processing/:jobId": Processing,
    "/report/:jobId": Report
  };
</script>

<Layout>
  <Router {routes} />
</Layout>