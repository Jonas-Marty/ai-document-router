import { ClipboardList, Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { LabHeader } from "@/components/lab/LabTabs";
import { MAX_TEST_SET_NAME, testSetNameError } from "@/components/lab/labFormat";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateTestSet, useTestSets } from "@/hooks/useLab";
import { ApiError } from "@/services/api/errors";

/** SPEC 8.13a: the test sets with their case count. */
export default function TestSetsPage() {
  const testSets = useTestSets();
  const [creating, setCreating] = useState(false);

  let body: React.ReactNode;
  if (testSets.isLoading) {
    body = (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  } else if (testSets.isError) {
    body = (
      <ErrorState
        message={
          testSets.error instanceof ApiError
            ? testSets.error.message
            : "Couldn't load the test sets."
        }
        onRetry={() => testSets.refetch()}
      />
    );
  } else if (!testSets.data?.length) {
    body = (
      <EmptyState
        icon={ClipboardList}
        title="No test sets yet"
        description="A test set is a handful of documents whose right name and folder you know: documents you filed, or files already in your archive."
        action={{ label: "New test set", onClick: () => setCreating(true) }}
      />
    );
  } else {
    body = (
      <ul className="divide-y rounded-lg border">
        {testSets.data.map((set) => (
          <li key={set.id}>
            <Link
              to={`/lab/test-sets/${set.id}`}
              className="flex min-h-12 items-center justify-between gap-3 px-3 py-2 hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <span className="min-w-0 font-medium break-words">{set.name}</span>
              <span className="shrink-0 text-sm text-muted-foreground tabular-nums">
                {set.case_count} {set.case_count === 1 ? "document" : "documents"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <LabHeader
        action={
          (testSets.data?.length ?? 0) > 0 && (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" />
              New test set
            </Button>
          )
        }
      />
      {body}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          {creating && <NewTestSetForm onClose={() => setCreating(false)} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function NewTestSetForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const create = useCreateTestSet();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const trimmed = name.trim();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const invalid = testSetNameError(name);
    if (invalid) {
      setError(invalid);
      return;
    }
    create.mutate(trimmed, {
      onSuccess: (set) => {
        onClose();
        navigate(`/lab/test-sets/${set.id}`);
      },
      onError: (failure) =>
        setError(failure instanceof ApiError ? failure.message : "Couldn't create the test set."),
    });
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <DialogHeader>
        <DialogTitle>New test set</DialogTitle>
        <DialogDescription>Add documents to it on the next page.</DialogDescription>
      </DialogHeader>
      <div className="space-y-1.5">
        <Label htmlFor="test-set-name">Name</Label>
        <Input
          id="test-set-name"
          value={name}
          maxLength={MAX_TEST_SET_NAME}
          aria-invalid={error !== null}
          aria-describedby={error ? "test-set-name-error" : undefined}
          onChange={(event) => {
            setName(event.target.value);
            setError(null);
          }}
        />
        {error && (
          <p id="test-set-name-error" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
          Create
        </Button>
      </DialogFooter>
    </form>
  );
}
