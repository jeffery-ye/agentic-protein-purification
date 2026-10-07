<script lang="ts">
  import { Badge, type BadgeVariant } from "$lib/components/ui/badge";
  import { STATE_LABELS } from "../../lib/progress";
  import type { JobState } from "../../lib/types/api";

  interface Props {
    state: JobState;
  }

  let { state }: Props = $props();

  const VARIANTS: Record<JobState, BadgeVariant> = {
    queued: "secondary",
    running: "default",
    completed: "outline",
    failed: "destructive",
    interrupted: "secondary"
  };
</script>

<Badge variant={VARIANTS[state]} class={state === "interrupted" ? "bg-yellow-100 text-yellow-900" : ""}>
  {STATE_LABELS[state]}
</Badge>
