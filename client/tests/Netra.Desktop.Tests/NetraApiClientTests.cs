using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Xunit;

namespace Netra.Desktop.Tests;

// NetraApiClient against a stub HttpMessageHandler: the exact requests it
// sends (method, path, bearer header, snake_case body) and how it parses the
// server's shapes (api/src/netra_api/transport/http/sessions.py). The live
// counterpart runs against the real server in LiveServerTests.
public sealed class NetraApiClientTests
{
    private const string Token = "test-token-not-real";
    private static readonly Uri Base = new("http://127.0.0.1:5000/");

    private const string SnapshotJson = """
        {"session_version":1,"interaction_mode":"reading","active_source_version_id":"5b3e9c1a-7d2f-4e8a-9b61-0c4d2f8a1e37",
         "current_block_id":null,"current_sentence_id":null,"last_acknowledged_sentence_id":null,
         "active_lesson":null,"pending_question":null,"last_result_set":null}
        """;

    [Fact]
    public async Task CreateSession_PostsWithTheBearerCredentialAndParsesTheSnapshot()
    {
        var handler = new StubHandler();
        var sessionId = Guid.NewGuid();
        handler.Respond(HttpStatusCode.Created, $$"""{"session_id":"{{sessionId}}","snapshot":{{SnapshotJson}}}""");
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler);

        var created = await client.CreateSessionAsync(CancellationToken.None);

        var request = Assert.Single(handler.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("/v1/sessions", request.Uri.AbsolutePath);
        Assert.Equal($"Bearer {Token}", request.Authorization);
        Assert.DoesNotContain(Token, request.Uri.ToString());
        Assert.Equal(sessionId, created.SessionId);
        Assert.Equal(1, created.Snapshot.SessionVersion);
        Assert.Equal(SessionInteractionMode.Reading, created.Snapshot.InteractionMode);
    }

    [Fact]
    public async Task WithoutACredential_NothingIsSent()
    {
        var handler = new StubHandler();
        using var client = new NetraApiClient(Base, new FixedCredentials(null), handler);

        await Assert.ThrowsAsync<CredentialUnavailableException>(() => client.CreateSessionAsync(CancellationToken.None));

        Assert.Empty(handler.Requests);
    }

    [Fact]
    public async Task ListSources_ParsesReadyAndNotYetReadySources()
    {
        var handler = new StubHandler();
        handler.Respond(HttpStatusCode.OK, """
            {"sources":[
              {"source_id":"s-1","title":"Ohm's law chapter","active_source_version_id":"v-1","active_version_number":2},
              {"source_id":"s-2","title":"Still parsing","active_source_version_id":null,"active_version_number":null}]}
            """);
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler);
        var sessionId = Guid.NewGuid();

        var sources = await client.ListSourcesAsync(sessionId, CancellationToken.None);

        Assert.Equal($"/v1/sessions/{sessionId}/sources", handler.Requests[0].Uri.AbsolutePath);
        Assert.Equal(HttpMethod.Get, handler.Requests[0].Method);
        Assert.Equal(new ApiSource("s-1", "Ohm's law chapter", "v-1", 2), sources[0]);
        Assert.Equal(new ApiSource("s-2", "Still parsing", null, null), sources[1]);
    }

    [Fact]
    public async Task SelectSource_SendsTheServersSnakeCaseFieldsAndReportsReplay()
    {
        var handler = new StubHandler();
        handler.Respond(HttpStatusCode.OK, $$"""{"snapshot":{{SnapshotJson}},"replayed":true}""");
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler);
        var sessionId = Guid.NewGuid();
        var requestId = Guid.NewGuid();

        var selected = await client.SelectSourceAsync(
            sessionId, requestId, "5b3e9c1a-7d2f-4e8a-9b61-0c4d2f8a1e37", 0, CancellationToken.None);

        var request = Assert.Single(handler.Requests);
        Assert.Equal($"/v1/sessions/{sessionId}/source", request.Uri.AbsolutePath);
        using var body = JsonDocument.Parse(request.Body!);
        var fields = body.RootElement.EnumerateObject().Select(p => p.Name).OrderBy(n => n).ToArray();
        // Exactly SourceSelection's fields (extra="forbid" on the server).
        Assert.Equal(new[] { "expected_session_version", "request_id", "source_version_id" }, fields);
        Assert.Equal(requestId.ToString(), body.RootElement.GetProperty("request_id").GetString());
        Assert.Equal(0, body.RootElement.GetProperty("expected_session_version").GetInt64());
        Assert.True(selected.Replayed);
        Assert.Equal("5b3e9c1a-7d2f-4e8a-9b61-0c4d2f8a1e37", selected.Snapshot.ActiveSourceVersionId);
    }

    [Fact]
    public async Task AVersionConflict_SurfacesTheTypedErrorWithTheServersCurrentVersion()
    {
        var handler = new StubHandler();
        handler.Respond(HttpStatusCode.Conflict, """
            {"error":{"code":"SESSION_VERSION_CONFLICT","message":"The session changed.","retryable":false,"current_session_version":3}}
            """);
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler);

        var ex = await Assert.ThrowsAsync<ApiErrorException>(() => client.SelectSourceAsync(
            Guid.NewGuid(), Guid.NewGuid(), "v-1", 0, CancellationToken.None));

        Assert.Equal(409, ex.StatusCode);
        Assert.Equal(ErrorCode.SessionVersionConflict, ex.Error?.Code);
        Assert.Equal(3, ex.Error?.CurrentSessionVersion);
    }

    [Fact]
    public async Task ANonContractErrorBody_IsReportedByStatusOnly()
    {
        var handler = new StubHandler();
        handler.Respond(HttpStatusCode.BadGateway, "<html>proxy error: internal detail</html>", "text/html");
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler);

        var ex = await Assert.ThrowsAsync<ApiErrorException>(() => client.CreateSessionAsync(CancellationToken.None));

        Assert.Equal(502, ex.StatusCode);
        Assert.Null(ex.Error);
        Assert.DoesNotContain("internal detail", ex.Message);
    }

    [Theory]
    [InlineData("wss://netra.example/v1/ws", "https://netra.example/")]
    [InlineData("ws://127.0.0.1:8123/v1/ws", "http://127.0.0.1:8123/")]
    public void HttpBase_IsTheSameAuthorityAsTheValidatedSocketEndpoint(string socket, string expected)
    {
        Assert.Equal(new Uri(expected), NetraApiClient.HttpBaseFor(new Uri(socket)));
    }

    [Fact]
    public void HttpBase_KeepsAPathPrefixInFrontOfTheSocketPath()
    {
        var httpBase = NetraApiClient.HttpBaseFor(new Uri("wss://school.example/netra/v1/ws"));

        Assert.Equal(new Uri("https://school.example/netra/"), httpBase);
        Assert.Equal(new Uri("https://school.example/netra/v1/sessions"), new Uri(httpBase, "v1/sessions"));
    }

    [Fact]
    public async Task ARequestThatOutlivesTheClientTimeout_EndsAsATimeoutNotAHang()
    {
        var handler = new StubHandler { Delay = TimeSpan.FromSeconds(30) };
        handler.Respond(HttpStatusCode.OK, """{"sources":[]}""");
        using var client = new NetraApiClient(Base, new FixedCredentials(Token), handler, requestTimeout: TimeSpan.FromMilliseconds(100));

        var ex = await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.ListSourcesAsync(Guid.NewGuid(), CancellationToken.None));

        Assert.IsType<TimeoutException>(ex.InnerException);
        Assert.Equal(
            "Timed out trying to load your sources. Try again.",
            Netra.Desktop.ViewModels.FailureText.Describe(ex, "load your sources", CancellationToken.None));
    }

    [Theory]
    [InlineData("ws://netra.example/v1/ws")] // plain ws only to loopback
    [InlineData("wss://netra.example/v1/ws?token=x")] // nothing may ride in the URL
    public void HttpBase_RefusesEndpointsTheSocketWouldRefuse(string socket)
    {
        Assert.Throws<InvalidServerEndpointException>(() => NetraApiClient.HttpBaseFor(new Uri(socket)));
    }

    internal sealed class FixedCredentials : ICredentialSource
    {
        private readonly string? _token;

        public FixedCredentials(string? token) => _token = token;

        public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) => ValueTask.FromResult(_token);
    }

    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly Queue<(HttpStatusCode Status, string Body, string MediaType)> _responses = new();

        public List<(HttpMethod Method, Uri Uri, string? Authorization, string? Body)> Requests { get; } = new();

        public TimeSpan Delay { get; set; } = TimeSpan.Zero;

        public void Respond(HttpStatusCode status, string body, string mediaType = "application/json") =>
            _responses.Enqueue((status, body, mediaType));

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var body = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
            Requests.Add((request.Method, request.RequestUri!, request.Headers.Authorization?.ToString(), body));
            await Task.Delay(Delay, cancellationToken);
            var (status, text, mediaType) = _responses.Dequeue();
            return new HttpResponseMessage(status) { Content = new StringContent(text, Encoding.UTF8, mediaType) };
        }
    }
}
