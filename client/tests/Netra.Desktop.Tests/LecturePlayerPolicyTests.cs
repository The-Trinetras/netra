using Netra.Desktop.Video;
using Xunit;

namespace Netra.Desktop.Tests;

// What may reach the lecture player: a validated 11-character YouTube id,
// Netra's own player page at the top level, YouTube embed frames and nothing
// else (M5-VIDEO: locked to the YouTube embed origin).
public sealed class LecturePlayerPolicyTests
{
    [Theory]
    [InlineData("dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123", "dQw4w9WgXcQ")]
    [InlineData("youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ")]
    [InlineData("  https://www.youtube-nocookie.com/embed/a_b-c1D2e3F  ", "a_b-c1D2e3F")]
    public void AcceptsAYouTubeIdOrLink(string text, string expected)
    {
        Assert.True(YouTubeVideoId.TryParseLink(text, out var id));
        Assert.Equal(expected, id!.Value);
    }

    [Theory]
    [InlineData("")]
    [InlineData("dQw4w9WgXc")]
    [InlineData("dQw4w9WgXcQQ")]
    [InlineData("dQw4w9WgX\"Q")]
    [InlineData("https://vimeo.com/dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ")]
    [InlineData("https://www.youtube.com/playlist?list=PL123")]
    [InlineData("https://www.youtube.com/watch?v=dQw4w9WgXcQ');alert(1)//")]
    [InlineData("javascript:alert('dQw4w9WgXcQ')")]
    [InlineData("ftp://youtu.be/dQw4w9WgXcQ")]
    [InlineData("ask about ohm's law")]
    public void RefusesEverythingElse(string text)
    {
        Assert.False(YouTubeVideoId.TryParseLink(text, out var id));
        Assert.Null(id);
    }

    [Fact]
    public void TopLevelIsOnlyNetrasOwnPlayerPage()
    {
        Assert.True(PlayerNavigationPolicy.IsAllowedTopLevel("https://netra-player.example/player.html"));
        Assert.True(PlayerNavigationPolicy.IsAllowedTopLevel("https://NETRA-PLAYER.example/player.html"));
        foreach (var other in new[]
        {
            "http://netra-player.example/player.html",
            "https://netra-player.example/player.html?x=1",
            "https://netra-player.example/player.html#frag",
            "https://netra-player.example/other.html",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
            "https://example.com/",
            "about:blank",
            null,
        })
        {
            Assert.False(PlayerNavigationPolicy.IsAllowedTopLevel(other), other);
        }
    }

    [Fact]
    public void FramesAreOnlyYouTubeEmbeds()
    {
        Assert.True(PlayerNavigationPolicy.IsAllowedFrame("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1&origin=https%3A%2F%2Fnetra-player.example"));
        Assert.True(PlayerNavigationPolicy.IsAllowedFrame("https://www.youtube.com/embed/dQw4w9WgXcQ"));
        Assert.True(PlayerNavigationPolicy.IsAllowedFrame("about:blank"));
        foreach (var other in new[]
        {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/",
            "https://accounts.google.com/ServiceLogin",
            "http://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/not-an-id",
            "https://youtube.com.evil.example/embed/dQw4w9WgXcQ",
            "https://example.com/embed/dQw4w9WgXcQ",
            null,
        })
        {
            Assert.False(PlayerNavigationPolicy.IsAllowedFrame(other), other);
        }
    }

    [Fact]
    public void OnlyThePlayerPageMayTalkToNetra()
    {
        Assert.True(PlayerNavigationPolicy.IsTrustedMessageSource("https://netra-player.example/player.html"));
        Assert.False(PlayerNavigationPolicy.IsTrustedMessageSource("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"));
    }

    [Theory]
    [InlineData(0, "0 seconds", "0:00")]
    [InlineData(999, "0 seconds", "0:00")]
    [InlineData(1_000, "1 second", "0:01")]
    [InlineData(48_250, "48 seconds", "0:48")]
    [InlineData(185_000, "3 minutes 5 seconds", "3:05")]
    [InlineData(60_000, "1 minute", "1:00")]
    [InlineData(3_725_000, "1 hour 2 minutes 5 seconds", "1:02:05")]
    [InlineData(7_200_000, "2 hours", "2:00:00")]
    public void TimesAreSpokenInWords(long ms, string words, string clock)
    {
        Assert.Equal(words, SpokenTime.Words(ms));
        Assert.Equal(clock, SpokenTime.Clock(ms));
    }
}
