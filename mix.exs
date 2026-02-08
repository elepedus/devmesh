defmodule DevMesh.MixProject do
  use Mix.Project

  def project do
    [
      app: :dev_mesh,
      version: "0.1.0",
      elixir: "~> 1.15",
      start_permanent: false,
      deps: deps(),
      description: "Local dev service mesh integration for Phoenix via Caddy reverse proxy",
      package: package()
    ]
  end

  def application do
    [
      extra_applications: [:logger]
    ]
  end

  defp deps do
    [
      {:req, "~> 0.5"}
    ]
  end

  defp package do
    [
      licenses: ["MIT"],
      links: %{}
    ]
  end
end
