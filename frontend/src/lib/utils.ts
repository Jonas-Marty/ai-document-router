import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** For a SelectTrigger that must stay inside its container: shadcn's trigger never wraps and
 * never shrinks, so a long choice (a configuration named after its model) would widen a
 * dialog past the screen. This lets it shrink and cuts the value with an ellipsis. */
export const SHRINKING_SELECT_TRIGGER =
  "w-full min-w-0 *:data-[slot=select-value]:block! *:data-[slot=select-value]:min-w-0 *:data-[slot=select-value]:truncate";
