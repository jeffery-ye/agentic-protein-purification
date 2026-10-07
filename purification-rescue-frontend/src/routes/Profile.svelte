<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { signOutUrl } from "../lib/api/client";
  import { userStore } from "../lib/stores/user";
</script>

<div class="w-full max-w-xl space-y-6">
  <h2 class="border-b border-gray-200 pb-2 text-2xl font-normal text-foreground">Profile</h2>

  {#if $userStore.user}
    {@const user = $userStore.user}
    <dl class="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
      <dt class="font-medium">Username</dt>
      <dd>{user.username}</dd>
      <dt class="font-medium">Email</dt>
      <dd>{user.email || "-"}</dd>
    </dl>

    <div class="flex flex-wrap gap-3">
      {#if user.can_change_password && user.change_password_url}
        <Button variant="outline" href={user.change_password_url}>Change password</Button>
      {/if}
      {#if user.auth_enabled}
        <Button variant="outline" href={signOutUrl}>Sign out</Button>
      {/if}
    </div>

    {#if !user.auth_enabled}
      <p class="text-sm text-muted-foreground">Sign-in is off here, so there is no password to change or session to end.</p>
    {/if}
  {:else if $userStore.status === "error"}
    <p class="text-sm text-destructive">Could not load your profile.</p>
  {:else}
    <p class="text-sm text-muted-foreground">Loading...</p>
  {/if}
</div>
