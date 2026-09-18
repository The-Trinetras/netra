using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Networking;

// Typed client for the session/source HTTP routes M1 exposes next to /v1/ws
// (api/src/netra_api/transport/http/sessions.py; M1's route proposal, pending
// formal M1/M5 sign-off):
//   POST /v1/sessions                          -> session id + session.snapshot
//   GET  /v1/sessions/{id}/sources             -> the account's sources
//   POST /v1/sessions/{id}/source              -> explicit pin (request_id replay)
// Every request carries `Authorization: Bearer` from ICredentialSource; the
// token is never put in a URL, logged or kept beyond the request. Failures
// surface the typed `error` payload (code/message/retryable), never raw text.
public sealed record ApiSource(string SourceId, string Title, string? ActiveSourceVersionId, int? ActiveVersionNumber);

public sealed record ApiSessionCreated(Guid SessionId, SessionSnapshotPayload Snapshot);

public sealed record ApiSourceSelected(SessionSnapshotPayload Snapshot, bool Replayed);

public sealed class ApiErrorException : Exception
{
    public ApiErrorException(int statusCode, ErrorPayload? error)
        : base(error?.Message ?? "The server could not complete the request.")
    {
        StatusCode = statusCode;
        Error = error;
    }

    public int StatusCode { get; }
    public ErrorPayload? Error { get; }
}

public interface INetraApi
{
    Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken);
    Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken);

    // requestId is minted once per logical selection and reused verbatim on a
    // retransmission, exactly like a navigation command.
    Task<ApiSourceSelected> SelectSourceAsync(
        Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken);
}

public sealed class NetraApiClient : INetraApi, IDisposable
{
    // Client-local bound on one HTTP request (no wire field; not a product
    // value): the student hears a timeout instead of a silent 100 s wait.
    public static readonly TimeSpan DefaultRequestTimeout = TimeSpan.FromSeconds(15);

    private const string SocketPath = "v1/ws";

    private readonly HttpClient _http;
    private readonly ICredentialSource _credentials;

    public NetraApiClient(
        Uri httpBase, ICredentialSource credentials, HttpMessageHandler? handler = null, TimeSpan? requestTimeout = null)
    {
        _http = handler is null ? new HttpClient() : new HttpClient(handler, disposeHandler: false);
        _http.BaseAddress = httpBase;
        _http.Timeout = requestTimeout ?? DefaultRequestTimeout;
        _credentials = credentials;
    }

    // The HTTP base that serves the same API as a validated WebSocket
    // endpoint: wss -> https, ws (loopback only) -> http, same authority, and
    // any path prefix in front of /v1/ws kept (e.g. behind a reverse proxy).
    public static Uri HttpBaseFor(Uri webSocketEndpoint)
    {
        var endpoint = ServerEndpoint.Validate(webSocketEndpoint);
        var scheme = endpoint.Scheme == "wss" ? Uri.UriSchemeHttps : Uri.UriSchemeHttp;
        var path = endpoint.AbsolutePath;
        var basePath = path.EndsWith("/" + SocketPath, StringComparison.Ordinal) ? path[..^SocketPath.Length] : "/";
        return new UriBuilder(scheme, endpoint.Host, endpoint.Port, basePath).Uri;
    }

    public async Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken)
    {
        using var response = await SendAsync(HttpMethod.Post, "v1/sessions", body: null, cancellationToken).ConfigureAwait(false);
        var created = await ReadAsync<CreatedBody>(response, cancellationToken).ConfigureAwait(false);
        return new ApiSessionCreated(created.SessionId, created.Snapshot);
    }

    public async Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(HttpMethod.Get, $"v1/sessions/{sessionId}/sources", body: null, cancellationToken)
            .ConfigureAwait(false);
        var listed = await ReadAsync<SourcesBody>(response, cancellationToken).ConfigureAwait(false);
        return listed.Sources
            .Select(s => new ApiSource(s.SourceId, s.Title, s.ActiveSourceVersionId, s.ActiveVersionNumber))
            .ToList();
    }

    public async Task<ApiSourceSelected> SelectSourceAsync(
        Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken)
    {
        var body = new SelectBody(requestId, sourceVersionId, expectedSessionVersion);
        using var response = await SendAsync(HttpMethod.Post, $"v1/sessions/{sessionId}/source", body, cancellationToken)
            .ConfigureAwait(false);
        var selected = await ReadAsync<SelectedBody>(response, cancellationToken).ConfigureAwait(false);
        return new ApiSourceSelected(selected.Snapshot, selected.Replayed);
    }

    private async Task<HttpResponseMessage> SendAsync(
        HttpMethod method, string path, object? body, CancellationToken cancellationToken)
    {
        var token = await _credentials.GetBearerTokenAsync(cancellationToken).ConfigureAwait(false);
        if (string.IsNullOrEmpty(token))
        {
            throw new CredentialUnavailableException();
        }

        using var request = new HttpRequestMessage(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (body is not null)
        {
            request.Content = JsonContent.Create(body, body.GetType(), options: NetraJsonSerialization.Options);
        }

        var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            ErrorPayload? error = null;
            try
            {
                var wrapper = await response.Content
                    .ReadFromJsonAsync<ErrorBody>(NetraJsonSerialization.Options, cancellationToken).ConfigureAwait(false);
                error = wrapper?.Error;
            }
            catch (JsonException)
            {
                // A non-contract body (e.g. a proxy page) is reported by status only.
            }

            var status = (int)response.StatusCode;
            response.Dispose();
            throw new ApiErrorException(status, error);
        }

        return response;
    }

    private static async Task<T> ReadAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        var value = await response.Content.ReadFromJsonAsync<T>(NetraJsonSerialization.Options, cancellationToken)
            .ConfigureAwait(false);
        return value ?? throw new ProtocolException("The server returned an empty response.");
    }

    public void Dispose() => _http.Dispose();

    private sealed record CreatedBody(Guid SessionId, SessionSnapshotPayload Snapshot);

    private sealed record SourcesBody(IReadOnlyList<SourceItem> Sources);

    private sealed record SourceItem(string SourceId, string Title, string? ActiveSourceVersionId, int? ActiveVersionNumber);

    private sealed record SelectBody(Guid RequestId, string SourceVersionId, long ExpectedSessionVersion);

    private sealed record SelectedBody(SessionSnapshotPayload Snapshot, bool Replayed);

    private sealed record ErrorBody(ErrorPayload? Error);
}
