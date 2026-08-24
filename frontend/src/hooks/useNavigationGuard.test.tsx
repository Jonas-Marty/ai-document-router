import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  NavigationGuardProvider,
  useConfirmNavigation,
  useSetNavigationGuard,
} from "./useNavigationGuard";

function Setter({ message }: { message: string | null }) {
  useSetNavigationGuard(message);
  return null;
}

function NavButton() {
  const confirmNavigation = useConfirmNavigation();
  const [navigated, setNavigated] = useState(false);
  return (
    <button type="button" onClick={() => confirmNavigation() && setNavigated(true)}>
      {navigated ? "navigated" : "not navigated"}
    </button>
  );
}

function Harness({ message }: { message: string | null }) {
  return (
    <StrictMode>
      <NavigationGuardProvider>
        <Setter message={message} />
        <NavButton />
      </NavigationGuardProvider>
    </StrictMode>
  );
}

describe("useNavigationGuard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("allows navigation without ever calling window.confirm when unguarded", async () => {
    // jsdom has no real confirm() -- if the unguarded path reached it, this would throw or
    // return undefined and the click below would report "not navigated".
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<Harness message={null} />);

    await user.click(screen.getByRole("button"));

    expect(screen.getByRole("button")).toHaveTextContent("navigated");
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("prompts with the guard message and blocks navigation when the user cancels", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<Harness message="You have unsaved changes." />);

    await user.click(screen.getByRole("button"));

    expect(confirmSpy).toHaveBeenCalledWith("You have unsaved changes.");
    expect(screen.getByRole("button")).toHaveTextContent("not navigated");
  });

  it("allows navigation once the user confirms", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<Harness message="You have unsaved changes." />);

    await user.click(screen.getByRole("button"));

    expect(screen.getByRole("button")).toHaveTextContent("navigated");
  });

  it("disarms once the setter unmounts or passes null again", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    const { rerender } = render(<Harness message="You have unsaved changes." />);

    rerender(<Harness message={null} />);
    await user.click(screen.getByRole("button"));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("button")).toHaveTextContent("navigated");
  });

  it("registers a beforeunload handler only while a message is armed", () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const { rerender, unmount } = render(<Harness message={null} />);

    expect(addSpy).not.toHaveBeenCalledWith("beforeunload", expect.any(Function));

    rerender(<Harness message="You have unsaved changes." />);
    expect(addSpy).toHaveBeenCalledWith("beforeunload", expect.any(Function));

    unmount();
    expect(removeSpy).toHaveBeenCalledWith("beforeunload", expect.any(Function));
  });
});
