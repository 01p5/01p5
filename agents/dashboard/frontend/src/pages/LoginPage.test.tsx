import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { LoginPage } from "./LoginPage";
import { api } from "../api";

const STATUS = {
  bypass: false, google_oauth: true, email_otp: true,
  allowed_domains: ["stanford.edu", "tianleyu.com"], cookie_secure: false,
};

afterEach(() => { vi.restoreAllMocks(); cleanup(); });

function renderLogin() {
  return render(<MemoryRouter initialEntries={["/login"]}><LoginPage /></MemoryRouter>);
}

beforeEach(() => {
  vi.spyOn(api, "me").mockResolvedValue({ authenticated: false, auth: STATUS });
});

describe("LoginPage", () => {
  it("renders Google + email options when both are configured", async () => {
    renderLogin();
    expect(await screen.findByText(/continue with google/i)).toBeInTheDocument();
    expect(await screen.findByPlaceholderText(/you@stanford.edu/i)).toBeInTheDocument();
    // Allowed-domain hint surfaces.
    expect(screen.getByText(/stanford\.edu, tianleyu\.com/)).toBeInTheDocument();
  });

  it("hides Google when not configured", async () => {
    vi.spyOn(api, "me").mockResolvedValue({
      authenticated: false,
      auth: { ...STATUS, google_oauth: false },
    });
    renderLogin();
    await waitFor(() => screen.getByPlaceholderText(/you@stanford.edu/i));
    expect(screen.queryByText(/continue with google/i)).not.toBeInTheDocument();
  });

  it("hides email form when not configured", async () => {
    vi.spyOn(api, "me").mockResolvedValue({
      authenticated: false,
      auth: { ...STATUS, email_otp: false },
    });
    renderLogin();
    expect(await screen.findByText(/continue with google/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/you@stanford.edu/i)).not.toBeInTheDocument();
  });

  it("shows the empty state when neither method is configured", async () => {
    vi.spyOn(api, "me").mockResolvedValue({
      authenticated: false,
      auth: { ...STATUS, google_oauth: false, email_otp: false },
    });
    renderLogin();
    expect(await screen.findByText(/no sign-in methods/i)).toBeInTheDocument();
  });

  it("email-start success advances to code stage", async () => {
    vi.spyOn(api, "emailStart").mockResolvedValue({ sent: true, email: "a@stanford.edu" });
    renderLogin();
    const emailInput = await screen.findByPlaceholderText(/you@stanford.edu/i);
    await userEvent.type(emailInput, "a@stanford.edu");
    await userEvent.click(screen.getByRole("button", { name: /send sign-in code/i }));
    // Now in code-entry stage.
    expect(await screen.findByText(/we sent a 6-digit code/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("123456")).toBeInTheDocument();
  });

  it("email-start error surfaces a friendly message", async () => {
    vi.spyOn(api, "emailStart").mockRejectedValue(new Error("403 Forbidden: email_not_allowed"));
    renderLogin();
    const emailInput = await screen.findByPlaceholderText(/you@stanford.edu/i);
    await userEvent.type(emailInput, "eve@gmail.com");
    await userEvent.click(screen.getByRole("button", { name: /send sign-in code/i }));
    expect(await screen.findByText(/domain isn't on the allowlist/i)).toBeInTheDocument();
  });

  it("verify error surfaces invalid-code message", async () => {
    vi.spyOn(api, "emailStart").mockResolvedValue({ sent: true, email: "a@stanford.edu" });
    vi.spyOn(api, "emailVerify").mockRejectedValue(new Error("401 Unauthorized: invalid_or_expired_code"));
    renderLogin();
    await userEvent.type(await screen.findByPlaceholderText(/you@stanford.edu/i), "a@stanford.edu");
    await userEvent.click(screen.getByRole("button", { name: /send sign-in code/i }));
    const codeInput = await screen.findByPlaceholderText("123456");
    await userEvent.type(codeInput, "999999");
    await userEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByText(/wrong or expired code/i)).toBeInTheDocument();
  });
});
