using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class ClientSessionStateTests
{
    [Fact]
    public void TryAdvanceSessionVersion_RejectsNonIncreasingVersion()
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), sessionVersion: 5);

        Assert.True(state.TryAdvanceSessionVersion(6));
        Assert.False(state.TryAdvanceSessionVersion(6));
        Assert.Equal(6, state.SessionVersion);
    }

    [Fact]
    public void TryMarkApplied_RejectsDuplicateRequestId()
    {
        var state = new ClientSessionState();
        var requestId = Guid.NewGuid();

        Assert.True(state.TryMarkApplied(requestId));
        Assert.False(state.TryMarkApplied(requestId));
    }
}
