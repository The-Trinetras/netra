using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Networking;

public sealed record DeviceCredential(string Token, DateTimeOffset? ExpiresAt);

// Decision D-CRED: a one-time access code, exchanged once by the app for a
// device credential. The route (C2) is Arshad's to draft; until it merges,
// HttpAccessCodeExchange is the client's side of the proposal recorded in
// docs/team/handoffs/M5.md ("C2 review from the client side").
public interface IAccessCodeExchange
{
    // requestId is minted once per sign-in attempt and reused verbatim on a
    // retransmission, so a lost response cannot burn the one-time code.
    Task<DeviceCredential> ExchangeAsync(Guid requestId, string accessCode, CancellationToken cancellationToken);
}

// The route is missing on this server (404/405): it predates C2.
public sealed class AccessCodeExchangeUnavailableException : Exception
{
    public AccessCodeExchangeUnavailableException()
        : base("This Netra server does not accept access codes.")
    {
    }
}

public sealed class HttpAccessCodeExchange : IAccessCodeExchange, IDisposable
{
    // Proposed in the C2 review; changes here if C2 names it differently.
    public const string RoutePath = "v1/access-codes/exchange";

    private readonly HttpClient _http;

    public HttpAccessCodeExchange(Uri httpBase, HttpMessageHandler? handler = null, TimeSpan? requestTimeout = null)
    {
        _http = handler is null ? new HttpClient() : new HttpClient(handler, disposeHandler: false);
        _http.BaseAddress = httpBase;
        _http.Timeout = requestTimeout ?? NetraApiClient.DefaultRequestTimeout;
    }

    // A transport failure or client timeout (no response seen: the code may
    // or may not have been used) is retried once under the SAME request id;
    // typed server errors and caller cancellation are never retried.
    public async Task<DeviceCredential> ExchangeAsync(Guid requestId, string accessCode, CancellationToken cancellationToken)
    {
        try
        {
            return await SendAsync(requestId, accessCode, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is HttpRequestException
                                   || (ex is TaskCanceledException && !cancellationToken.IsCancellationRequested))
        {
            return await SendAsync(requestId, accessCode, cancellationToken).ConfigureAwait(false);
        }
    }

    private async Task<DeviceCredential> SendAsync(Guid requestId, string accessCode, CancellationToken cancellationToken)
    {
        // No Authorization header: the code is the credential here. It is in
        // the body only, never in the URL, and is never logged.
        using var request = new HttpRequestMessage(HttpMethod.Post, RoutePath)
        {
            Content = JsonContent.Create(new ExchangeBody(requestId, accessCode), options: NetraJsonSerialization.Options),
        };
        using var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.MethodNotAllowed)
        {
            throw new AccessCodeExchangeUnavailableException();
        }

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
            }

            throw new ApiErrorException((int)response.StatusCode, error);
        }

        ExchangedBody? body;
        try
        {
            body = await response.Content
                .ReadFromJsonAsync<ExchangedBody>(NetraJsonSerialization.Options, cancellationToken).ConfigureAwait(false);
        }
        catch (JsonException ex)
        {
            throw new ProtocolException("The server's sign-in response could not be read.", ex);
        }

        if (body is null || string.IsNullOrWhiteSpace(body.DeviceCredential))
        {
            throw new ProtocolException("The server's sign-in response carried no credential.");
        }

        return new DeviceCredential(body.DeviceCredential, body.ExpiresAt);
    }

    public void Dispose() => _http.Dispose();

    private sealed record ExchangeBody(Guid RequestId, string AccessCode);

    private sealed record ExchangedBody(string DeviceCredential, DateTimeOffset? ExpiresAt);

    private sealed record ErrorBody(ErrorPayload? Error);
}
