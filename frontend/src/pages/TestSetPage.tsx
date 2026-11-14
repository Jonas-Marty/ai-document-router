import { ArrowLeft, FolderPlus, ListPlus, Loader2, Play, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { FolderPicker } from "@/components/folders/FolderPicker";
import { AddFromDoneDialog } from "@/components/lab/AddFromDoneDialog";
import { LabTabs } from "@/components/lab/LabTabs";
import { MAX_TEST_SET_NAME, testSetNameError } from "@/components/lab/labFormat";
import { NewRunDialog } from "@/components/lab/NewRunDialog";
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
import {
  useAddTestCases,
  useDeleteTestCase,
  useDeleteTestSet,
  useRenameTestSet,
  useTestSet,
} from "@/hooks/useLab";
import { ApiError } from "@/services/api/errors";
import type { AddCasesRequest, TestCase, TestSetDetail } from "@/services/api/types";

function failed(fallback: string) {
  return (error: Error) => toast.error(error instanceof ApiError ? error.message : fallback);
}

function addedMessage(added: number, skipped: number): string {
  const what = `Added ${added} ${added === 1 ? "document" : "documents"}`;
  return skipped ? `${what}, skipped ${skipped}` : what;
}

/** SPEC 8.13a: one test set -- its name, its documents, and the ways to add more. */
export default function TestSetPage() {
  const { id = "" } = useParams();
  const testSet = useTestSet(id);

  let body: React.ReactNode;
  if (testSet.isLoading) {
    body = (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  } else if (testSet.isError || !testSet.data) {
    const notFound = testSet.error instanceof ApiError && testSet.error.status === 404;
    body = (
      <ErrorState
        message={
          notFound
            ? "This test set no longer exists."
            : testSet.error instanceof ApiError
              ? testSet.error.message
              : "Couldn't load the test set."
        }
        onRetry={notFound ? undefined : () => testSet.refetch()}
      />
    );
  } else {
    // Keyed so a rename form never keeps the name of the set before.
    body = <TestSetBody key={testSet.data.id} testSet={testSet.data} />;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <div className="space-y-3">
        <h1 className="text-lg font-semibold">Lab</h1>
        <LabTabs />
      </div>
      <Link
        to="/lab/test-sets"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Test sets
      </Link>
      {body}
    </div>
  );
}

function TestSetBody({ testSet }: { testSet: TestSetDetail }) {
  const navigate = useNavigate();
  const add = useAddTestCases();
  const removeCase = useDeleteTestCase();
  const removeSet = useDeleteTestSet();
  const [doneOpen, setDoneOpen] = useState(false);
  const [folderOpen, setFolderOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  function addCases(body: AddCasesRequest, close: () => void) {
    add.mutate(
      { id: testSet.id, body },
      {
        onSuccess: ({ added, skipped }) => {
          close();
          toast.success(addedMessage(added, skipped));
        },
        onError: failed("Couldn't add the documents."),
      },
    );
  }

  return (
    <>
      <RenameForm testSet={testSet} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setDoneOpen(true)}>
          <ListPlus aria-hidden="true" />
          Add from Done
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setFolderOpen(true)}>
          <FolderPlus aria-hidden="true" />
          Add a folder
        </Button>
        {testSet.cases.length > 0 && (
          <Button type="button" size="sm" onClick={() => setRunOpen(true)}>
            <Play aria-hidden="true" />
            Run this set
          </Button>
        )}
      </div>

      {testSet.cases.length === 0 ? (
        <EmptyState
          title="No documents yet"
          description="Add documents you filed, or a folder of your archive whose files are already named the way you want."
        />
      ) : (
        <ul className="divide-y rounded-lg border">
          {testSet.cases.map((testCase) => (
            <CaseRow
              key={testCase.id}
              testCase={testCase}
              disabled={removeCase.isPending}
              onRemove={() =>
                removeCase.mutate(testCase.id, {
                  onError: failed("Couldn't remove the document."),
                })
              }
            />
          ))}
        </ul>
      )}

      <div>
        <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmingDelete(true)}>
          Delete test set
        </Button>
      </div>

      <AddFromDoneDialog
        open={doneOpen}
        onOpenChange={setDoneOpen}
        isAdding={add.isPending}
        onAdd={(ids) => addCases({ document_ids: ids }, () => setDoneOpen(false))}
      />
      <FolderPicker
        open={folderOpen}
        onOpenChange={setFolderOpen}
        value=""
        onSelect={(path) => addCases({ folder_path: path }, () => undefined)}
      />
      <NewRunDialog open={runOpen} onOpenChange={setRunOpen} testSetId={testSet.id} />
      <Dialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {testSet.name}?</DialogTitle>
            <DialogDescription>
              Its documents leave the Lab; the files themselves are not touched. Runs of it stay.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={removeSet.isPending}
              onClick={() =>
                removeSet.mutate(testSet.id, {
                  onSuccess: () => {
                    toast.success(`Deleted ${testSet.name}`);
                    navigate("/lab/test-sets");
                  },
                  onError: failed("Couldn't delete the test set."),
                  onSettled: () => setConfirmingDelete(false),
                })
              }
            >
              {removeSet.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function RenameForm({ testSet }: { testSet: TestSetDetail }) {
  const rename = useRenameTestSet();
  const [name, setName] = useState(testSet.name);
  const [error, setError] = useState<string | null>(null);
  const changed = name.trim() !== testSet.name;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const invalid = testSetNameError(name);
    if (invalid) {
      setError(invalid);
      return;
    }
    rename.mutate(
      { id: testSet.id, name: name.trim() },
      {
        onSuccess: (saved) => {
          setName(saved.name);
          toast.success("Renamed the test set");
        },
        onError: (failure) =>
          setError(failure instanceof ApiError ? failure.message : "Couldn't rename the set."),
      },
    );
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-1.5">
      <Label htmlFor="test-set-name">Name</Label>
      <div className="flex gap-2">
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
        {changed && (
          <Button type="submit" variant="outline" disabled={rename.isPending}>
            Save
          </Button>
        )}
      </div>
      {error && (
        <p id="test-set-name-error" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </form>
  );
}

function CaseRow({
  testCase,
  disabled,
  onRemove,
}: {
  testCase: TestCase;
  disabled: boolean;
  onRemove: () => void;
}) {
  return (
    <li className="flex items-start gap-2 px-3 py-2">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="font-mono text-xs break-words sm:text-sm">{testCase.expected_name}</p>
        <p className="font-mono text-xs break-words text-muted-foreground">
          {testCase.expected_folder}
        </p>
        <p className="text-xs text-muted-foreground">
          {testCase.source === "done"
            ? `Filed · scanned as ${testCase.original_filename}`
            : "From the archive"}
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="shrink-0"
        aria-label={`Remove ${testCase.expected_name}`}
        disabled={disabled}
        onClick={onRemove}
      >
        <X aria-hidden="true" />
      </Button>
    </li>
  );
}
