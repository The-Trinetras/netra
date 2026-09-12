using System.Text.Json;
using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// Decision F (2026-09-12): request_id is minted once per logical user
// action and reused verbatim across a retransmit of that same action;
// message_id is fresh per transmission. Exercised against the actual
// ConnectionManager send path, not a standalone helper.
public sealed class ConnectionManagerRetryTests
{
    [Fact]
    public async Task ResendUsesTheSameRequestIdAsTheOriginalSend()
    {
        var (connectionManager, socket) = Build();

        await connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload { Command = NavigationCommandType.Next, ExpectedSessionVersion = 4 },
            CancellationToken.None);
        var originalRequestId = ExtractRequestId(socket.SentMessages[0]);

        var resent = await connectionManager.ResendPendingMutatingRequestAsync(CancellationToken.None);

        Assert.True(resent);
        Assert.Equal(2, socket.SentMessages.Count);
        Assert.Equal(originalRequestId, ExtractRequestId(socket.SentMessages[1]));
    }

    [Fact]
    public async Task ResendUsesADifferentMessageIdThanTheOriginalSend()
    {
        var (connectionManager, socket) = Build();

        await connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload { Command = NavigationCommandType.Next, ExpectedSessionVersion = 4 },
            CancellationToken.None);
        var originalMessageId = ExtractMessageId(socket.SentMessages[0]);

        await connectionManager.ResendPendingMutatingRequestAsync(CancellationToken.None);

        var resentMessageId = ExtractMessageId(socket.SentMessages[1]);
        Assert.NotEqual(originalMessageId, resentMessageId);
    }

    [Fact]
    public async Task NothingToResendWhenNoMutationIsOutstanding()
    {
        var (connectionManager, _) = Build();

        var resent = await connectionManager.ResendPendingMutatingRequestAsync(CancellationToken.None);

        Assert.False(resent);
    }

    [Fact]
    public async Task AResponseCorrelatingToTheRequestClearsItSoALaterResendIsANoOp()
    {
        var (connectionManager, socket) = Build();

        await connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload { Command = NavigationCommandType.Next, ExpectedSessionVersion = 4 },
            CancellationToken.None);
        var requestId = ExtractRequestId(socket.SentMessages[0]);

        socket.RaiseTextMessageReceived(ServerEnvelopeJson(requestId));

        var resent = await connectionManager.ResendPendingMutatingRequestAsync(CancellationToken.None);
        Assert.False(resent);
    }

    [Fact]
    public async Task ANewActionAfterAPriorOneCompletesMintsItsOwnRequestId()
    {
        var (connectionManager, socket) = Build();

        await connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload { Command = NavigationCommandType.Next, ExpectedSessionVersion = 4 },
            CancellationToken.None);
        var firstRequestId = ExtractRequestId(socket.SentMessages[0]);

        await connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload { Command = NavigationCommandType.Previous, ExpectedSessionVersion = 5 },
            CancellationToken.None);
        var secondRequestId = ExtractRequestId(socket.SentMessages[1]);

        Assert.NotEqual(firstRequestId, secondRequestId);
    }

    [Fact]
    public async Task SessionResumeIsNotTrackedAsAPendingMutatingRequest()
    {
        var (connectionManager, _) = Build();

        await connectionManager.SendSessionResumeAsync(
            new SessionResumePayload { LastKnownSessionVersion = 1 }, CancellationToken.None);

        var resent = await connectionManager.ResendPendingMutatingRequestAsync(CancellationToken.None);
        Assert.False(resent);
    }

    private static (ConnectionManager Manager, RecordingWebSocketClient Socket) Build()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var socket = new RecordingWebSocketClient();
        var connectionManager = new ConnectionManager(socket, sessionState);
        return (connectionManager, socket);
    }

    private static Guid ExtractRequestId(string json) =>
        Guid.Parse(JsonDocument.Parse(json).RootElement.GetProperty("request_id").GetString()!);

    private static Guid ExtractMessageId(string json) =>
        Guid.Parse(JsonDocument.Parse(json).RootElement.GetProperty("message_id").GetString()!);

    private static string ServerEnvelopeJson(Guid requestId) =>
        $$"""
        {
          "protocol_version": "1.0",
          "message_id": "{{Guid.NewGuid()}}",
          "session_id": "{{Guid.NewGuid()}}",
          "request_id": "{{requestId}}",
          "sequence": 1,
          "type": "error",
          "payload": {
            "code": "SESSION_VERSION_CONFLICT",
            "message": "stale",
            "retryable": false
          }
        }
        """;

    private sealed class RecordingWebSocketClient : INetraWebSocketClient
    {
        public List<string> SentMessages { get; } = new();

        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void RaiseTextMessageReceived(string json) => TextMessageReceived?.Invoke(this, json);

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken)
        {
            SentMessages.Add(message);
            return Task.CompletedTask;
        }

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
