import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** A saved/typed value that the endpoint's list may not (yet, or ever) contain still belongs
 * in the options -- dropping it would blank a working setting the moment the picker appeared,
 * or before it has fetched anything at all. */
export function optionsWithCurrent(models: string[] | undefined, current: string): string[] {
  const known = models ?? [];
  return current && !known.includes(current) ? [current, ...known] : known;
}

/** One model dropdown, used once per step of every task chain. Fetching is lazy -- opening
 * the dropdown is what triggers it -- so `onOpen` fires every time and it is up to the caller
 * to no-op once that endpoint's list has arrived. Free text stays reachable behind a toggle:
 * an endpoint that cannot be reached, or one that does not list the model you actually want,
 * must still be configurable. */
export function ModelPicker({
  id,
  ariaLabel,
  value,
  onChange,
  options,
  disabled,
  isPending,
  errorMessage,
  onOpen,
  manualMode,
  onToggleManual,
}: {
  id?: string;
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  disabled: boolean;
  isPending: boolean;
  errorMessage: string | null;
  onOpen: () => void;
  manualMode: boolean;
  onToggleManual: () => void;
}) {
  if (manualMode) {
    return (
      <div className="space-y-1">
        <Input
          id={id}
          aria-label={ariaLabel}
          className="font-mono"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button
          type="button"
          variant="link"
          className="h-auto p-0 text-sm"
          onClick={onToggleManual}
        >
          Choose from the endpoint's list
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <Select
        value={value}
        onValueChange={onChange}
        disabled={disabled}
        onOpenChange={(open) => {
          if (open) onOpen();
        }}
      >
        <SelectTrigger id={id} aria-label={ariaLabel} className="w-full font-mono">
          {isPending ? (
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              Loading models…
            </span>
          ) : (
            <SelectValue placeholder="Choose a model" />
          )}
        </SelectTrigger>
        <SelectContent>
          {/* An item still renders for the current value even on error or an empty result --
           * dropping it would leave Radix with nothing to resolve the trigger's label from,
           * blanking a perfectly valid, already-saved model name. */}
          {errorMessage && <p className="px-2 py-1.5 text-sm text-destructive">{errorMessage}</p>}
          {options.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">No models found.</p>
          ) : (
            options.map((model) => (
              <SelectItem key={model} value={model} className="font-mono">
                {model}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <Button type="button" variant="link" className="h-auto p-0 text-sm" onClick={onToggleManual}>
        Enter a model name manually
      </Button>
    </div>
  );
}
