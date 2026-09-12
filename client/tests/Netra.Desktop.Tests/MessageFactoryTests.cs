using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class MessageFactoryTests
{
    [Fact]
    public void CreateTurnSubmit_SetsProtocolEnvelopeFields()
    {
        var sessionId = Guid.NewGuid();
        var requestId = Guid.NewGuid();

        var envelope = MessageFactory.CreateTurnSubmit(
            sessionId,
            requestId,
            sequence: 1,
            new TurnSubmitPayload
            {
                Utterance = "Explain congestion control.",
                InputMode = InputMode.Voice,
                ExpectedSessionVersion = 4,
            });

        Assert.Equal(NetraProtocol.Version, envelope.ProtocolVersion);
        Assert.Equal(sessionId, envelope.SessionId);
        Assert.Equal(requestId, envelope.RequestId);
        Assert.Equal(ClientMessageType.TurnSubmit, envelope.Type);
    }

    [Fact]
    public void Serialize_UsesSnakeCaseFieldNamesAndDottedTypeValues()
    {
        var envelope = MessageFactory.CreateNavigationCommand(
            Guid.NewGuid(),
            Guid.NewGuid(),
            sequence: 2,
            new NavigationCommandPayload { Command = NavigationCommandType.WhereAmI, ExpectedSessionVersion = 1 });

        var json = MessageFactory.Serialize(envelope);

        Assert.Contains("\"navigation.command\"", json);
        Assert.Contains("\"expected_session_version\"", json);
        Assert.Contains("\"where_am_i\"", json);
    }
}
