import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useNextcloud, useTestNextcloud, useUpdateNextcloud } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { NextcloudWrite } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import { type NextcloudFormValues, nextcloudFormSchema } from "./settingsSchemas";

/** The account's own Nextcloud: everything else in the app reads from and files into it.
 *
 * CLAUDE.md rule 5: the app password is write-only. The field always starts empty, and an
 * empty field on save or test means "use the stored one", never "clear it". */
export function NextcloudSection({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const nextcloud = useNextcloud();
  const update = useUpdateNextcloud();
  const test = useTestNextcloud();
  const passwordSet = nextcloud.data?.password_set === true;

  const values = useMemo(
    () => ({
      base_url: nextcloud.data?.base_url ?? "",
      username: nextcloud.data?.username ?? "",
      password: "",
    }),
    [nextcloud.data],
  );
  const {
    register,
    handleSubmit,
    getValues,
    reset,
    setError,
    formState: { errors, isDirty },
  } = useForm<NextcloudFormValues>({
    resolver: zodResolver(nextcloudFormSchema),
    values,
    resetOptions: { keepDirtyValues: true },
  });

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function toBody(form: NextcloudFormValues): NextcloudWrite {
    const password = form.password.trim();
    return {
      base_url: form.base_url.trim(),
      username: form.username.trim(),
      ...(password ? { password } : {}),
    };
  }

  function onSubmit(form: NextcloudFormValues) {
    if (!passwordSet && !form.password.trim()) {
      setError("password", { message: "Enter an app password." });
      return;
    }
    update.mutate(toBody(form), {
      onSuccess: (saved) => {
        reset(
          { base_url: saved.base_url, username: saved.username, password: "" },
          { keepDirtyValues: false },
        );
        toast.success("Nextcloud connection saved");
      },
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't save the connection.");
      },
    });
  }

  function onTest() {
    test.mutate(toBody(getValues()), {
      onSuccess: () => toast.success("Signed in to Nextcloud"),
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't reach Nextcloud.");
      },
    });
  }

  if (nextcloud.isLoading) return <Skeleton className="h-64 w-full" />;
  if (nextcloud.isError) {
    return (
      <ErrorState
        message={
          nextcloud.error instanceof ApiError
            ? nextcloud.error.message
            : "Couldn't load the Nextcloud connection."
        }
        onRetry={() => nextcloud.refetch()}
      />
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="Nextcloud"
        isDirty={isDirty}
        isSaving={update.isPending}
        onDiscard={() => reset(values, { keepDirtyValues: false })}
      >
        <p className="text-sm text-muted-foreground">
          Where your scans arrive and where they are filed. Use an app password, created in
          Nextcloud under Personal settings → Security, not your login password.
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="nextcloud_base_url">WebDAV URL</Label>
          <Input
            id="nextcloud_base_url"
            className="font-mono"
            placeholder="https://cloud.example.com/remote.php/dav/files/you"
            aria-invalid={!!errors.base_url}
            {...register("base_url")}
          />
          {errors.base_url && <p className="text-sm text-destructive">{errors.base_url.message}</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="nextcloud_username">Username</Label>
          <Input
            id="nextcloud_username"
            autoComplete="off"
            aria-invalid={!!errors.username}
            {...register("username")}
          />
          {errors.username && <p className="text-sm text-destructive">{errors.username.message}</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="nextcloud_password">App password</Label>
          <Input
            id="nextcloud_password"
            type="password"
            autoComplete="new-password"
            placeholder={passwordSet ? "••••••••  (saved)" : ""}
            aria-invalid={!!errors.password}
            {...register("password")}
          />
          {errors.password ? (
            <p className="text-sm text-destructive">{errors.password.message}</p>
          ) : (
            passwordSet && (
              <p className="text-sm text-muted-foreground">
                Leave blank to keep the current app password.
              </p>
            )
          )}
        </div>
        <Button type="button" variant="outline" disabled={test.isPending} onClick={onTest}>
          {test.isPending && <Loader2 className="animate-spin" />}
          Test connection
        </Button>
      </SectionCard>
    </form>
  );
}
