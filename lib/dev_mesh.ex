defmodule DevMesh do
  @moduledoc """
  Local dev service mesh integration for Phoenix.

  Registers your Phoenix app with a Caddy reverse proxy on startup,
  giving it a unique HTTPS URL via Unix socket. Optionally proxies
  Tidewave Web through Caddy on port 9833.

  ## Usage

      defmodule MyApp.DevProxy do
        use DevMesh,
          route_id: "my-app",
          otp_app: :my_app,
          endpoint: MyAppWeb.Endpoint,
          fallback_port: 4000
      end

  Then add to your supervision tree (before the Endpoint):

      children =
        [
          MyAppWeb.Telemetry,
          {Phoenix.PubSub, name: MyApp.PubSub}
        ] ++
          DevMesh.children(MyApp.DevProxy) ++
          [MyAppWeb.Endpoint]

  ## Options

    * `:route_id` - Required. Subdomain identifier (e.g. "my-app").
    * `:otp_app` - Required. Your application atom (e.g. :my_app).
    * `:endpoint` - Required. Your Phoenix Endpoint module.
    * `:fallback_port` - Required. TCP port when Caddy is unavailable.
    * `:caddy_admin` - Caddy admin API URL. Default: "http://localhost:2019".
    * `:sock_dir` - Unix socket directory. Default: "/tmp/caddy-dev".
    * `:tidewave` - Enable Tidewave Web proxy on port 9833. Default: true.
    * `:tidewave_upstream` - Tidewave Web address. Default: "localhost:9832".
  """

  defmacro __using__(opts) do
    quote bind_quoted: [opts: opts] do
      use GenServer
      require Logger

      @route_id Keyword.fetch!(opts, :route_id)
      @otp_app Keyword.fetch!(opts, :otp_app)
      @endpoint Keyword.fetch!(opts, :endpoint)
      @fallback_port Keyword.fetch!(opts, :fallback_port)

      @caddy_admin Keyword.get(opts, :caddy_admin, "http://localhost:2019")
      @sock_dir Keyword.get(opts, :sock_dir, "/tmp/caddy-dev")
      @sock_path Path.join(@sock_dir, "#{@route_id}.sock")

      @tidewave_enabled Keyword.get(opts, :tidewave, true)
      @tidewave_route_id "tidewave-#{@route_id}"
      @tidewave_upstream Keyword.get(opts, :tidewave_upstream, "localhost:9832")

      def start_link(_opts), do: GenServer.start_link(__MODULE__, [], name: __MODULE__)

      @impl true
      def init(_) do
        case DevMesh.discover_domain(@caddy_admin) do
          {:ok, domain} ->
            DevMesh.cleanup_socket(@sock_path)
            DevMesh.deregister(@caddy_admin, @route_id)
            DevMesh.configure_endpoint(@otp_app, @endpoint, @sock_path, @route_id, domain)
            DevMesh.register(@caddy_admin, @route_id, @sock_path, domain)

            if @tidewave_enabled do
              DevMesh.register_tidewave(
                @caddy_admin,
                @tidewave_route_id,
                @route_id,
                @tidewave_upstream,
                domain
              )
            end

            Logger.info("dev-mesh: https://#{@route_id}.#{domain}")
            {:ok, %{domain: domain}}

          :error ->
            Logger.info(
              "dev-mesh: Caddy not available, using http://localhost:#{@fallback_port}"
            )

            {:ok, %{domain: nil}}
        end
      end

      @impl true
      def terminate(_reason, %{domain: domain}) when is_binary(domain) do
        DevMesh.deregister(@caddy_admin, @route_id)

        if @tidewave_enabled do
          DevMesh.deregister_tidewave(@caddy_admin, @tidewave_route_id)
        end

        :ok
      end

      def terminate(_, _), do: :ok
    end
  end

  @doc """
  Returns supervisor children for DevProxy (dev-only).

  Use in your Application supervisor:

      children =
        [...] ++
          DevMesh.children(MyApp.DevProxy) ++
          [MyAppWeb.Endpoint]
  """
  def children(dev_proxy_module) do
    if Mix.env() == :dev, do: [dev_proxy_module], else: []
  end

  @doc false
  def discover_domain(caddy_admin) do
    case Req.get("#{caddy_admin}/config/apps/tls/", receive_timeout: 2000, retry: false) do
      {:ok, %{status: 200, body: body}} ->
        subjects = get_in(body, ["certificates", "automate"]) || []

        case Enum.find(subjects, &String.starts_with?(&1, "*.")) do
          "*." <> domain -> {:ok, domain}
          _ -> :error
        end

      _ ->
        :error
    end
  end

  @doc false
  def configure_endpoint(otp_app, endpoint, sock_path, route_id, domain) do
    config = Application.get_env(otp_app, endpoint)

    updated =
      config
      |> Keyword.put(:http, ip: {:local, sock_path}, port: 0)
      |> Keyword.put(:url, host: "#{route_id}.#{domain}", scheme: "https", port: 443)

    Application.put_env(otp_app, endpoint, updated)
  end

  @doc false
  def cleanup_socket(sock_path), do: File.rm(sock_path)

  @doc false
  def register(caddy_admin, route_id, sock_path, domain) do
    Req.post("#{caddy_admin}/config/apps/http/servers/srv0/routes",
      json: %{
        "@id" => route_id,
        "match" => [%{"host" => ["#{route_id}.#{domain}"]}],
        "handle" => [
          %{
            "handler" => "reverse_proxy",
            "upstreams" => [%{"dial" => "unix/#{sock_path}"}]
          }
        ]
      }
    )
  end

  @doc false
  def deregister(caddy_admin, route_id) do
    Req.delete("#{caddy_admin}/id/#{route_id}")
  rescue
    _ -> :ok
  end

  @doc false
  def register_tidewave(caddy_admin, tidewave_route_id, route_id, tidewave_upstream, domain) do
    ensure_tidewave_server(caddy_admin)
    deregister_tidewave(caddy_admin, tidewave_route_id)

    Req.post("#{caddy_admin}/config/apps/http/servers/tidewave/routes",
      json: %{
        "@id" => tidewave_route_id,
        "match" => [%{"host" => ["#{route_id}.#{domain}"]}],
        "handle" => [
          %{
            "handler" => "reverse_proxy",
            "headers" => %{
              "request" => %{
                "set" => %{
                  "Origin" => ["http://#{tidewave_upstream}"]
                }
              }
            },
            "upstreams" => [%{"dial" => tidewave_upstream}]
          }
        ]
      },
      retry: false
    )
  end

  @doc false
  def ensure_tidewave_server(caddy_admin) do
    case Req.get("#{caddy_admin}/config/apps/http/servers/tidewave", retry: false) do
      {:ok, %{status: 200, body: body}} when is_map(body) -> :ok
      _ ->
        Req.put("#{caddy_admin}/config/apps/http/servers/tidewave",
          json: %{"listen" => [":9833"], "routes" => []},
          retry: false
        )
    end
  end

  @doc false
  def deregister_tidewave(caddy_admin, tidewave_route_id) do
    Req.delete("#{caddy_admin}/id/#{tidewave_route_id}", retry: false)
  rescue
    _ -> :ok
  end
end
