import type { Configuration } from "@/services/api/types";
import { describeFiling, describeReading } from "./configurationSchema";

/** How a configuration reads and files, in the Lab card's words: configurations are often
 * named after one of their models, so the name alone does not say what the other step uses. */
export function ConfigurationSteps({ configuration }: { configuration: Configuration }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
      <dt className="text-muted-foreground">Reading</dt>
      <dd className="font-mono break-words">{describeReading(configuration)}</dd>
      <dt className="text-muted-foreground">Filing</dt>
      <dd className="font-mono break-words">{describeFiling(configuration)}</dd>
    </dl>
  );
}
