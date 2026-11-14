import { FlaskConical, Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { describeFiling, describeReading } from "@/components/lab/configurationSchema";
import { LabHeader } from "@/components/lab/LabTabs";
import { LastRunsComparison } from "@/components/lab/LastRunsComparison";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAiEndpoints,
  useConfigurations,
  useDeleteConfiguration,
  useDuplicateConfiguration,
  useMakeDefaultConfiguration,
} from "@/hooks/useAiConfig";
import { ApiError } from "@/services/api/errors";
import type { Configuration } from "@/services/api/types";

type Confirming = { action: "default" | "delete"; configuration: Configuration } | null;

/** SPEC 8.13: the configurations, one card each. */
export default function LabConfigurationsPage() {
  const navigate = useNavigate();
  const configurations = useConfigurations();
  const endpoints = useAiEndpoints();
  const duplicate = useDuplicateConfiguration();
  const makeDefault = useMakeDefaultConfiguration();
  const remove = useDeleteConfiguration();
  const [confirming, setConfirming] = useState<Confirming>(null);

  function failed(fallback: string) {
    return (error: Error) => toast.error(error instanceof ApiError ? error.message : fallback);
  }

  function onDuplicate(configuration: Configuration) {
    duplicate.mutate(configuration.id, {
      onSuccess: (copy) => toast.success(`Added ${copy.name}`),
      onError: failed("Couldn't duplicate the configuration."),
    });
  }

  function onConfirm() {
    if (!confirming) return;
    const { action, configuration } = confirming;
    const done = { onSettled: () => setConfirming(null) };
    if (action === "default") {
      makeDefault.mutate(configuration.id, {
        ...done,
        onSuccess: () => toast.success(`${configuration.name} is now the default`),
        onError: failed("Couldn't change the default."),
      });
    } else {
      remove.mutate(configuration.id, {
        ...done,
        onSuccess: () => toast.success(`Removed ${configuration.name}`),
        onError: failed("Couldn't remove the configuration."),
      });
    }
  }

  const noConnections = endpoints.isSuccess && endpoints.data.length === 0;

  let body: React.ReactNode;
  if (configurations.isLoading) {
    body = (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  } else if (configurations.isError) {
    body = (
      <ErrorState
        message={
          configurations.error instanceof ApiError
            ? configurations.error.message
            : "Couldn't load the configurations."
        }
        onRetry={() => configurations.refetch()}
      />
    );
  } else if (!configurations.data?.length) {
    body = noConnections ? (
      <EmptyState
        icon={FlaskConical}
        title="Add a connection first"
        description="A configuration reads and files with models on your connections. Add one in Settings, then come back."
        action={{ label: "Go to Settings", onClick: () => navigate("/settings") }}
      />
    ) : (
      <EmptyState
        icon={FlaskConical}
        title="No configurations yet"
        description="Documents wait until there is one. The first you create becomes the default."
        action={{
          label: "New configuration",
          onClick: () => navigate("/lab/configurations/new"),
        }}
      />
    );
  } else {
    body = (
      <ul className="space-y-3">
        {configurations.data.map((configuration) => (
          <li key={configuration.id}>
            <ConfigurationCard
              configuration={configuration}
              isDuplicating={duplicate.isPending && duplicate.variables === configuration.id}
              onDuplicate={() => onDuplicate(configuration)}
              onMakeDefault={() => setConfirming({ action: "default", configuration })}
              onDelete={() => setConfirming({ action: "delete", configuration })}
            />
          </li>
        ))}
      </ul>
    );
  }

  const isConfirming = makeDefault.isPending || remove.isPending;

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <LabHeader
        action={
          !noConnections &&
          (configurations.data?.length ?? 0) > 0 && (
            <Button asChild size="sm">
              <Link to="/lab/configurations/new">
                <Plus aria-hidden="true" />
                New configuration
              </Link>
            </Button>
          )
        }
      />
      <p className="text-sm text-muted-foreground">
        A configuration is one way of reading and filing a document. New documents use the default;
        any document can be re-run with another.
      </p>
      {body}

      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirming?.action === "default"
                ? `Make ${confirming.configuration.name} the default?`
                : `Remove ${confirming?.configuration.name}?`}
            </DialogTitle>
            <DialogDescription>
              {confirming?.action === "default"
                ? "Documents waiting for an analysis switch to it. Proposals already made stay as they are."
                : "Documents that were re-run with it go back to the default. Proposals already made stay as they are."}
            </DialogDescription>
          </DialogHeader>
          {confirming?.action === "default" && (
            <LastRunsComparison
              candidate={confirming.configuration}
              current={configurations.data?.find((row) => row.is_default)}
            />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant={confirming?.action === "delete" ? "destructive" : "default"}
              disabled={isConfirming}
              onClick={onConfirm}
            >
              {isConfirming && <Loader2 className="animate-spin" aria-hidden="true" />}
              {confirming?.action === "default" ? "Make default" : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ConfigurationCard({
  configuration,
  isDuplicating,
  onDuplicate,
  onMakeDefault,
  onDelete,
}: {
  configuration: Configuration;
  isDuplicating: boolean;
  onDuplicate: () => void;
  onMakeDefault: () => void;
  onDelete: () => void;
}) {
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 font-medium break-words">{configuration.name}</h2>
          {configuration.is_default && <Badge>Default</Badge>}
        </div>
        <dl className="grid gap-1 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
          <dt className="text-muted-foreground">Reading</dt>
          <dd className="font-mono text-xs break-all sm:text-sm">
            {describeReading(configuration)}
          </dd>
          <dt className="text-muted-foreground">Filing</dt>
          <dd className="font-mono text-xs break-all sm:text-sm">
            {describeFiling(configuration)}
          </dd>
        </dl>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm" variant="outline">
            <Link to={`/lab/configurations/${configuration.id}`}>Edit</Link>
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={isDuplicating}
            onClick={onDuplicate}
          >
            Duplicate
          </Button>
          {!configuration.is_default && (
            <>
              <Button type="button" size="sm" variant="outline" onClick={onMakeDefault}>
                Make default
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
                Delete
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
