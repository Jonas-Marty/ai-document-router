import { Loader2, Pencil, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Textarea } from "@/components/ui/textarea";
import {
  useDeleteKnownAccount,
  useDeleteKnownSender,
  useKnownSenders,
  useUpdateKnownSender,
} from "@/hooks/useKnowledge";
import { validateField } from "@/lib/naming";
import { ApiError } from "@/services/api/errors";
import type { KnownSender } from "@/services/api/types";

function failed(fallback: string) {
  return (error: Error) => toast.error(error instanceof ApiError ? error.message : fallback);
}

/** SPEC 8.14: what approvals taught. It can be corrected and pruned here, never added to:
 * a list typed in beforehand would hold senders that never occur (SPEC 6.3c). */
export function SendersCard({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const senders = useKnownSenders();
  const removeAccount = useDeleteKnownAccount();
  const removeSender = useDeleteKnownSender();
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<KnownSender | null>(null);

  // An open edit form is unsaved work.
  useEffect(() => {
    onDirtyChange(editing !== null);
  }, [editing, onDirtyChange]);

  function onRemoveSender() {
    if (!confirming) return;
    const name = confirming.short_name;
    removeSender.mutate(confirming.id, {
      onSuccess: () => toast.success(`Forgot ${name}`),
      onError: failed("Couldn't remove the sender."),
      onSettled: () => setConfirming(null),
    });
  }

  let body: React.ReactNode;
  if (senders.isLoading) {
    body = <Skeleton className="h-20 w-full" />;
  } else if (senders.isError) {
    body = (
      <ErrorState
        message={
          senders.error instanceof ApiError ? senders.error.message : "Couldn't load the senders."
        }
        onRetry={() => senders.refetch()}
      />
    );
  } else if (!senders.data?.length) {
    body = (
      <p className="text-sm text-muted-foreground">
        None yet. When you approve a document from a sender this app doesn't know, the review form
        asks whether to remember it and its account.
      </p>
    );
  } else {
    body = (
      <ul className="space-y-2">
        {senders.data.map((sender) =>
          editing === sender.id ? (
            <li key={sender.id}>
              <SenderForm sender={sender} onDone={() => setEditing(null)} />
            </li>
          ) : (
            <li key={sender.id} className="space-y-2 rounded-md border border-border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 space-y-0.5">
                  <p className="font-medium break-words">{sender.short_name}</p>
                  {sender.aliases.length > 0 && (
                    <p className="text-sm break-words text-muted-foreground">
                      Written as {sender.aliases.join(" · ")}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Edit ${sender.short_name}`}
                    disabled={editing !== null}
                    onClick={() => setEditing(sender.id)}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`Remove ${sender.short_name}`}
                    onClick={() => setConfirming(sender)}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              </div>
              {sender.accounts.length > 0 && (
                <ul
                  className="flex flex-wrap gap-1"
                  aria-label={`Accounts of ${sender.short_name}`}
                >
                  {sender.accounts.map((account) => (
                    <li key={account.id}>
                      <Badge variant="secondary" className="gap-1 pr-0.5 font-mono break-all">
                        {account.value}
                        <button
                          type="button"
                          className="rounded-sm p-1 hover:bg-background/60"
                          aria-label={`Remove account ${account.value}`}
                          disabled={removeAccount.isPending}
                          onClick={() =>
                            removeAccount.mutate(account.id, {
                              onSuccess: () => toast.success(`Forgot ${account.value}`),
                              onError: failed("Couldn't remove the account."),
                            })
                          }
                        >
                          <X className="size-3" aria-hidden="true" />
                        </button>
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ),
        )}
      </ul>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Senders and accounts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Learned from your approvals. The model is given a sender's short name, and copies an
          account found in the document instead of reading its digits.
        </p>
        {body}
      </CardContent>

      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Forget {confirming?.short_name}?</DialogTitle>
            <DialogDescription>
              Its accounts go with it. Documents already filed stay as they are.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={removeSender.isPending}
              onClick={onRemoveSender}
            >
              {removeSender.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

/** Rename a sender, or say how it is written in documents (one way per line). */
function SenderForm({ sender, onDone }: { sender: KnownSender; onDone: () => void }) {
  const update = useUpdateKnownSender();
  const [shortName, setShortName] = useState(sender.short_name);
  const [aliases, setAliases] = useState(sender.aliases.join("\n"));
  const [error, setError] = useState<string | null>(null);
  const nameId = `sender-name-${sender.id}`;
  const aliasesId = `sender-aliases-${sender.id}`;

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const problem = validateField("sender", shortName);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    update.mutate(
      {
        id: sender.id,
        body: {
          short_name: shortName.trim(),
          aliases: aliases
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
      },
      {
        onSuccess: (saved) => {
          toast.success(`Saved ${saved.short_name}`);
          onDone();
        },
        onError: (failure) =>
          setError(failure instanceof ApiError ? failure.message : "Couldn't save the sender."),
      },
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-border p-3">
      <div className="space-y-1.5">
        <Label htmlFor={nameId}>Short name</Label>
        <Input
          id={nameId}
          value={shortName}
          aria-invalid={error !== null}
          onChange={(event) => setShortName(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={aliasesId}>Written in documents as</Label>
        <Textarea
          id={aliasesId}
          rows={3}
          value={aliases}
          aria-describedby={`${aliasesId}-help`}
          placeholder="Zürcher Kantonalbank"
          onChange={(event) => setAliases(event.target.value)}
        />
        <p id={`${aliasesId}-help`} className="text-sm text-muted-foreground">
          One per line, e.g. the legal name on the letterhead. Lets the model be told the short name
          when the document never uses it.
        </p>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={update.isPending} onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={update.isPending}>
          {update.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
          Save
        </Button>
      </div>
    </form>
  );
}
