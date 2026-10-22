import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ServerConfiguration } from "./server-configuration";
import type { ServerConfig } from "@/types";

// Issue #523: the Settings page reported the effective embedder mode but not
// *where it came from*, so an operator who edited config.json back and saw no
// change had nothing to go on. These pin the source line and the env-override
// warning the payload now carries.

function config(overrides: Partial<ServerConfig> = {}): ServerConfig {
  return {
    db_path: "/tmp/levh.db",
    embedder_mode: "hash",
    requested_embedder_mode: "hash",
    embedder_fallback_reason: null,
    embedder_mode_source: "environment variable EMBEDDER_MODE",
    env_overrides: [],
    embedder_dimension: 384,
    short_term_max: 50,
    weights: { alpha: 1, beta: 1, gamma: 1, delta: 1 },
    decay_half_life_hours: 24,
    reinforcement_gain: 0.1,
    max_stability_hours: 720,
    auto_summarize_sessions: false,
    version: "2.35.0",
    ...overrides,
  };
}

describe("ServerConfiguration", () => {
  it("names the layer that supplied the embedder mode", () => {
    render(<ServerConfiguration config={config()} />);
    expect(
      screen.getByText(/from environment variable EMBEDDER_MODE/),
    ).toBeInTheDocument();
  });

  it("warns when an env var overrides a conflicting config value", () => {
    render(
      <ServerConfiguration
        config={config({
          env_overrides: [
            {
              key: "embedder_mode",
              env_name: "EMBEDDER_MODE",
              env_value: "hash",
              file_value: "auto",
              config_path: "/home/u/.stackmemory/config.json",
            },
          ],
        })}
      />,
    );
    expect(
      screen.getByText(/Environment variables override config\.json/),
    ).toBeInTheDocument();
    expect(screen.getByText(/EMBEDDER_MODE=hash/)).toBeInTheDocument();
    expect(screen.getByText(/embedder_mode=auto/)).toBeInTheDocument();
  });

  it("shows no env-override warning when the layers agree", () => {
    render(<ServerConfiguration config={config()} />);
    expect(
      screen.queryByText(/Environment variables override config\.json/),
    ).not.toBeInTheDocument();
  });
});
