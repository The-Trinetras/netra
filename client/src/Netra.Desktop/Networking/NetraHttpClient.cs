using System.Net.Http;
using System.Net.Http.Json;
using System.Threading;
using Netra.Desktop.Protocol;

namespace Netra.Desktop.Networking;

// Thin JSON REST abstraction, separate from the WebSocket control channel.
// No endpoint is called from a static/startup path anywhere in this
// scaffold; construction alone makes no network request.
public interface INetraHttpClient
{
    Task<TResponse?> GetJsonAsync<TResponse>(string relativePath, CancellationToken cancellationToken);

    Task<TResponse?> PostJsonAsync<TRequest, TResponse>(
        string relativePath, TRequest body, CancellationToken cancellationToken);
}

public sealed class NetraHttpClient : INetraHttpClient, IDisposable
{
    private readonly HttpClient _httpClient;

    public NetraHttpClient(Uri baseAddress)
    {
        _httpClient = new HttpClient { BaseAddress = baseAddress };
    }

    public async Task<TResponse?> GetJsonAsync<TResponse>(string relativePath, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.GetAsync(relativePath, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<TResponse>(NetraJsonSerialization.Options, cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task<TResponse?> PostJsonAsync<TRequest, TResponse>(
        string relativePath, TRequest body, CancellationToken cancellationToken)
    {
        using var response = await _httpClient
            .PostAsJsonAsync(relativePath, body, NetraJsonSerialization.Options, cancellationToken)
            .ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<TResponse>(NetraJsonSerialization.Options, cancellationToken)
            .ConfigureAwait(false);
    }

    public void Dispose() => _httpClient.Dispose();
}
