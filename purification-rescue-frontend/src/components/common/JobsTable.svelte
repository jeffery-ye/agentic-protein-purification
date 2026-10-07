<script lang="ts">
  import {
    type ColumnDef,
    type PaginationState,
    type RowSelectionState,
    type SortingState,
    getCoreRowModel,
    getPaginationRowModel,
    getSortedRowModel
  } from "@tanstack/table-core";
  import { link, push } from "svelte-spa-router";
  import ArrowUpDownIcon from "@lucide/svelte/icons/arrow-up-down";
  import EllipsisIcon from "@lucide/svelte/icons/ellipsis";
  import { createSvelteTable, FlexRender, renderSnippet } from "$lib/components/ui/data-table";
  import * as Table from "$lib/components/ui/table";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu";
  import * as AlertDialog from "$lib/components/ui/alert-dialog";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { Button } from "$lib/components/ui/button";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import JobNameCell from "./JobNameCell.svelte";
  import JobStateBadge from "./JobStateBadge.svelte";
  import { formatRuntime, isActive, jobPath, jobsStore, jobTitle } from "../../lib/stores/jobs";
  import type { JobListItem } from "../../lib/types/api";

  const PAGE_SIZE = 25;

  let sorting = $state<SortingState>([{ id: "created_at", desc: true }]);
  let rowSelection = $state<RowSelectionState>({});
  let pagination = $state<PaginationState>({ pageIndex: 0, pageSize: PAGE_SIZE });

  let jobs = $derived($jobsStore.items);
  // Running jobs are pinned above the sorted rows, with live progress.
  let pinned = $derived(jobs.filter(isActive).map(job => job.id));

  // Forget selections of rows that are gone or have started running again.
  $effect(() => {
    const selectable = new Set(jobs.filter(job => !isActive(job)).map(job => job.id));
    const kept = Object.keys(rowSelection).filter(id => rowSelection[id] && selectable.has(id));
    if (kept.length !== Object.keys(rowSelection).length) {
      rowSelection = Object.fromEntries(kept.map(id => [id, true]));
    }
  });

  const columns: ColumnDef<JobListItem>[] = [
    {
      id: "select",
      header: ({ table }) => renderSnippet(selectAllCell, {
        checked: table.getIsAllPageRowsSelected(),
        indeterminate: table.getIsSomePageRowsSelected() && !table.getIsAllPageRowsSelected(),
        toggle: (value: boolean) => table.toggleAllPageRowsSelected(value)
      }),
      cell: ({ row }) => renderSnippet(selectCell, {
        job: row.original,
        checked: row.getIsSelected(),
        toggle: (value: boolean) => row.toggleSelected(value)
      }),
      enableSorting: false
    },
    {
      id: "name",
      accessorFn: jobTitle,
      header: ({ column }) => renderSnippet(sortHeader, { label: "Name", toggle: column.getToggleSortingHandler() }),
      cell: ({ row }) => renderSnippet(inputCell, row.original)
    },
    {
      accessorKey: "state",
      header: ({ column }) => renderSnippet(sortHeader, { label: "State", toggle: column.getToggleSortingHandler() }),
      cell: ({ row }) => renderSnippet(stateCell, row.original)
    },
    {
      accessorKey: "created_at",
      header: ({ column }) => renderSnippet(sortHeader, { label: "Created", toggle: column.getToggleSortingHandler() }),
      cell: ({ row }) => new Date(row.original.created_at).toLocaleString()
    },
    {
      accessorKey: "runtime_seconds",
      header: ({ column }) => renderSnippet(sortHeader, { label: "Runtime", toggle: column.getToggleSortingHandler() }),
      cell: ({ row }) => formatRuntime(row.original.runtime_seconds),
      sortUndefined: "last"
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => renderSnippet(actionsCell, row.original),
      enableSorting: false
    }
  ];

  const table = createSvelteTable({
    get data() {
      return jobs;
    },
    columns,
    getRowId: job => job.id,
    enableRowSelection: row => !isActive(row.original),
    enableRowPinning: true,
    keepPinnedRows: true,
    state: {
      get sorting() {
        return sorting;
      },
      get rowSelection() {
        return rowSelection;
      },
      get pagination() {
        return pagination;
      },
      get rowPinning() {
        return { top: pinned, bottom: [] };
      }
    },
    onSortingChange: updater => {
      sorting = typeof updater === "function" ? updater(sorting) : updater;
    },
    onRowSelectionChange: updater => {
      rowSelection = typeof updater === "function" ? updater(rowSelection) : updater;
    },
    onPaginationChange: updater => {
      pagination = typeof updater === "function" ? updater(pagination) : updater;
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel()
  });

  let selectedIds = $derived(Object.keys(rowSelection).filter(id => rowSelection[id]));

  // --- Deleting ------------------------------------------------------------

  let pendingDelete = $state<string[]>([]);
  let confirmOpen = $state(false);
  let deleting = $state(false);
  let deleteError = $state("");

  function askDelete(ids: string[]) {
    pendingDelete = ids;
    deleteError = "";
    confirmOpen = true;
  }

  async function confirmDelete() {
    deleting = true;
    deleteError = "";
    try {
      if (pendingDelete.length === 1) {
        await jobsStore.deleteOne(pendingDelete[0]);
      } else {
        const result = await jobsStore.deleteMany(pendingDelete);
        if (result.skipped.length) {
          deleteError = `${result.skipped.length} of ${pendingDelete.length} could not be deleted (running, or already gone).`;
        }
      }
      rowSelection = {};
      if (!deleteError) confirmOpen = false;
    } catch (err) {
      deleteError = err instanceof Error ? err.message : "Could not delete";
    } finally {
      deleting = false;
      void jobsStore.refresh();
    }
  }

  function open(job: JobListItem) {
    push(jobPath(job));
  }
</script>

{#snippet sortHeader({ label, toggle }: { label: string; toggle: ((event: unknown) => void) | undefined })}
  <Button variant="ghost" size="sm" class="-ml-2" onclick={toggle}>
    {label}
    <ArrowUpDownIcon class="text-muted-foreground" />
  </Button>
{/snippet}

{#snippet selectAllCell({ checked, indeterminate, toggle }: { checked: boolean; indeterminate: boolean; toggle: (value: boolean) => void })}
  <Checkbox
    {checked}
    {indeterminate}
    onCheckedChange={value => toggle(!!value)}
    aria-label="Select every finished job on this page"
  />
{/snippet}

{#snippet selectCell({ job, checked, toggle }: { job: JobListItem; checked: boolean; toggle: (value: boolean) => void })}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <span onclick={event => event.stopPropagation()}>
    {#if isActive(job)}
      <Tooltip.Root>
        <Tooltip.Trigger>
          {#snippet child({ props })}
            <span {...props}><Checkbox disabled aria-label="Running jobs can't be selected" /></span>
          {/snippet}
        </Tooltip.Trigger>
        <Tooltip.Content>A running job can't be deleted, because jobs can't be cancelled.</Tooltip.Content>
      </Tooltip.Root>
    {:else}
      <Checkbox {checked} onCheckedChange={value => toggle(!!value)} aria-label="Select job" />
    {/if}
  </span>
{/snippet}

{#snippet inputCell(job: JobListItem)}
  <JobNameCell {job} />
{/snippet}

{#snippet stateCell(job: JobListItem)}
  <div class="space-y-1">
    <JobStateBadge state={job.state} />
    {#if isActive(job) && job.progress}
      <div class="max-w-[260px] truncate text-xs text-muted-foreground" title={job.progress}>{job.progress}</div>
    {/if}
  </div>
{/snippet}

{#snippet actionsCell(job: JobListItem)}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <span onclick={event => event.stopPropagation()}>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        {#snippet child({ props })}
          <Button {...props} variant="ghost" size="icon-sm" aria-label="Job actions">
            <EllipsisIcon />
          </Button>
        {/snippet}
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end">
        <DropdownMenu.Item onSelect={() => open(job)}>Open</DropdownMenu.Item>
        <DropdownMenu.Item variant="destructive" disabled={isActive(job)} onSelect={() => askDelete([job.id])}>
          Delete
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  </span>
{/snippet}

{#snippet jobRow(row: ReturnType<typeof table.getRowModel>["rows"][number])}
  <Table.Row
    data-state={row.getIsSelected() ? "selected" : undefined}
    class="cursor-pointer {row.getIsPinned() ? 'bg-accent/40' : ''}"
    onclick={() => open(row.original)}
  >
    {#each row.getVisibleCells() as cell (cell.id)}
      <Table.Cell>
        <FlexRender content={cell.column.columnDef.cell} context={cell.getContext()} />
      </Table.Cell>
    {/each}
  </Table.Row>
{/snippet}

<Tooltip.Provider>
<div class="space-y-3">
  {#if selectedIds.length}
    <div class="flex items-center justify-between rounded-md border bg-muted/40 px-4 py-2 text-sm">
      <span>{selectedIds.length} selected</span>
      <Button variant="destructive" size="sm" onclick={() => askDelete(selectedIds)}>Delete</Button>
    </div>
  {/if}

  <div class="rounded-md border">
    <Table.Root>
      <Table.Header>
        {#each table.getHeaderGroups() as headerGroup (headerGroup.id)}
          <Table.Row>
            {#each headerGroup.headers as header (header.id)}
              <Table.Head class={header.column.id === "select" ? "w-10" : ""}>
                {#if !header.isPlaceholder}
                  <FlexRender content={header.column.columnDef.header} context={header.getContext()} />
                {/if}
              </Table.Head>
            {/each}
          </Table.Row>
        {/each}
      </Table.Header>
      <Table.Body>
        {#each table.getTopRows() as row (row.id)}
          {@render jobRow(row)}
        {/each}
        {#each table.getCenterRows() as row (row.id)}
          {@render jobRow(row)}
        {:else}
          {#if !table.getTopRows().length}
            <Table.Row>
              <Table.Cell colspan={columns.length} class="h-32 text-center">
                <p class="text-muted-foreground">No jobs yet.</p>
                <a href="/" use:link class="mt-2 inline-block text-primary underline">Start a new analysis</a>
              </Table.Cell>
            </Table.Row>
          {/if}
        {/each}
      </Table.Body>
    </Table.Root>
  </div>

  {#if table.getPageCount() > 1}
    <div class="flex items-center justify-end gap-2 text-sm">
      <span class="text-muted-foreground">Page {pagination.pageIndex + 1} of {table.getPageCount()}</span>
      <Button variant="outline" size="sm" disabled={!table.getCanPreviousPage()} onclick={() => table.previousPage()}>Previous</Button>
      <Button variant="outline" size="sm" disabled={!table.getCanNextPage()} onclick={() => table.nextPage()}>Next</Button>
    </div>
  {/if}
</div>
</Tooltip.Provider>

<AlertDialog.Root bind:open={confirmOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>
        Delete {pendingDelete.length === 1 ? "this job" : `${pendingDelete.length} jobs`}?
      </AlertDialog.Title>
      <AlertDialog.Description>
        {pendingDelete.length === 1 ? "The job, its inputs and its report" : "These jobs, their inputs and their reports"}
        will be deleted for good. This can't be undone.
      </AlertDialog.Description>
    </AlertDialog.Header>
    {#if deleteError}
      <p class="text-sm text-destructive">{deleteError}</p>
    {/if}
    <AlertDialog.Footer>
      <AlertDialog.Cancel disabled={deleting}>Cancel</AlertDialog.Cancel>
      <AlertDialog.Action variant="destructive" disabled={deleting} onclick={confirmDelete}>
        {deleting ? "Deleting..." : "Delete"}
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
