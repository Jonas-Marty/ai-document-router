import { useLayoutEffect, useRef, useState } from "react";

/** Tracks a container's content-box width via ResizeObserver. Used to fit the PDF page to
 * whatever space is actually available -- the mobile full-screen sheet and the desktop split
 * pane are very different widths, and only the container knows which. */
export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, width };
}
