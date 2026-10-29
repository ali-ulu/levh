import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./auth-gate";
import { getToken } from "@/lib/token";

const health = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { health: () => health() },
}));

describe("AuthGate", () => {
  beforeEach(() => {
    health.mockReset();
    window.localStorage.clear();
  });

  it("renders children when the server does not require a token", async () => {
    health.mockResolvedValue({ status: "ok", auth_required: false });
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    expect(await screen.findByText("secret dashboard")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Access token")).not.toBeInTheDocument();
  });

  it("shows the token card when a token is required and none is stored", async () => {
    health.mockResolvedValue({ status: "ok", auth_required: true });
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    expect(await screen.findByPlaceholderText("Access token")).toBeInTheDocument();
    expect(screen.queryByText("secret dashboard")).not.toBeInTheDocument();
  });

  it("tells the user where the token comes from and how to make one", async () => {
    // A locked screen with no recovery path is a dead end: the operator has to
    // learn that the value is the LEVH_TOKEN they launched with, and how to
    // generate one if they never set it.
    health.mockResolvedValue({ status: "ok", auth_required: true });
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    expect(await screen.findByText(/LEVH_TOKEN/)).toBeInTheDocument();
    expect(
      screen.getByText(/secrets\.token_urlsafe\(32\)/)
    ).toBeInTheDocument();
  });

  it("renders children when a token is required and one is already stored", async () => {
    window.localStorage.setItem("levh_token", "stored-token");
    health.mockResolvedValue({ status: "ok", auth_required: true });
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    expect(await screen.findByText("secret dashboard")).toBeInTheDocument();
  });

  it("fails open when the health check throws", async () => {
    health.mockRejectedValue(new Error("network down"));
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    expect(await screen.findByText("secret dashboard")).toBeInTheDocument();
  });

  it("stores the entered token and opens the gate", async () => {
    const user = userEvent.setup();
    health.mockResolvedValue({ status: "ok", auth_required: true });
    render(
      <AuthGate>
        <div>secret dashboard</div>
      </AuthGate>
    );
    await user.type(await screen.findByPlaceholderText("Access token"), "  typed-token  ");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    await waitFor(() => expect(getToken()).toBe("typed-token"));
    expect(await screen.findByText("secret dashboard")).toBeInTheDocument();
  });
});