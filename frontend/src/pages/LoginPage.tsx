import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthConfig, useLogin, useRegister } from "@/hooks/useAuth";
import { ApiError } from "@/services/api/errors";

const credentialsSchema = z.object({
  email: z.string().min(1, "Email is required."),
  password: z.string().min(1, "Password is required."),
});

type CredentialsValues = z.infer<typeof credentialsSchema>;

/** Sign-in, and — on an instance nobody has claimed yet — first-account setup.
 *
 * The same screen does both deliberately: a fresh deployment is reachable by anyone who
 * knows the URL until someone registers, so the shortest possible path from "deployed" to
 * "claimed" is the security-relevant one. The backend decides what is on offer
 * (`has_users`, `registration_open`); the form only renders it. */
export default function LoginPage() {
  const config = useAuthConfig();
  const login = useLogin();
  const register = useRegister();
  const [searchParams] = useSearchParams();
  // The OIDC callback cannot render a component, so it reports failures by redirecting here.
  const providerError = searchParams.get("error");

  const isSetup = config.data?.has_users === false;
  // ALLOW_REGISTRATION on a claimed instance. Sign-in stays the default even then: the
  // people who need an account are the exception, and the ones who already have one are
  // every other visit.
  const [wantsAccount, setWantsAccount] = useState(false);
  const canRegister = config.data?.registration_open === true;
  const isRegistering = isSetup || (canRegister && wantsAccount);
  const mutation = isRegistering ? register : login;

  const {
    register: field,
    handleSubmit,
    formState: { errors },
  } = useForm<CredentialsValues>({
    resolver: zodResolver(credentialsSchema),
    defaultValues: { email: "", password: "" },
  });

  const error = mutation.error;
  const message =
    error instanceof ApiError ? error.message : error ? "Something went wrong." : null;

  return (
    <div className="mx-auto flex min-h-full w-full max-w-md flex-col justify-center p-4">
      <Card>
        <CardHeader>
          <CardTitle>
            {isSetup ? "Create the first account" : isRegistering ? "Create an account" : "Sign in"}
          </CardTitle>
        </CardHeader>
        <form onSubmit={handleSubmit((values) => mutation.mutate(values))}>
          <CardContent className="space-y-4">
            {isSetup && (
              <p className="text-sm text-muted-foreground">
                Nobody has claimed this instance yet. The first account becomes its admin.
              </p>
            )}
            {providerError && (
              <Alert variant="destructive">
                <AlertDescription>{providerError}</AlertDescription>
              </Alert>
            )}
            {message && (
              <Alert variant="destructive">
                <AlertDescription>{message}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                aria-invalid={!!errors.email}
                {...field("email")}
              />
              {errors.email && <p className="text-sm text-destructive">{errors.email.message}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={isRegistering ? "new-password" : "current-password"}
                aria-invalid={!!errors.password}
                {...field("password")}
              />
              {errors.password && (
                <p className="text-sm text-destructive">{errors.password.message}</p>
              )}
              {isRegistering && (
                <p className="text-sm text-muted-foreground">At least 12 characters.</p>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex-col items-stretch gap-2">
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending && <Loader2 className="animate-spin" />}
              {isSetup
                ? "Create account and sign in"
                : isRegistering
                  ? "Create account"
                  : "Sign in"}
            </Button>
            {config.data?.oidc_enabled && (
              <Button variant="outline" asChild>
                {/* A full navigation, not fetch: the provider redirects the browser back. */}
                <a href="/api/v1/auth/oidc/login">
                  <KeyRound className="size-4" aria-hidden="true" />
                  Sign in with {config.data.oidc_provider_name}
                </a>
              </Button>
            )}
            {!isSetup && canRegister && (
              <Button
                type="button"
                variant="link"
                className="h-auto self-center py-0"
                onClick={() => {
                  setWantsAccount(!wantsAccount);
                  // The previous attempt's error belongs to the mode being left; carrying
                  // "Wrong email or password" onto a registration form would read as a
                  // verdict on what was just typed.
                  mutation.reset();
                }}
              >
                {wantsAccount ? "Already have an account? Sign in" : "Need an account? Create one"}
              </Button>
            )}
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
