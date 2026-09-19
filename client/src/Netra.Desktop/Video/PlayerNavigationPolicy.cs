namespace Netra.Desktop.Video;

// What the embedded lecture player may load (M5-VIDEO: locked to the YouTube
// embed origin; no other sites). The top-level document is only Netra's own
// bundled player page, served from a virtual host name mapped to the app
// folder; frames may only be YouTube embeds. Everything else is cancelled,
// and new windows, downloads and permissions are refused by the surface.
public static class PlayerNavigationPolicy
{
    // Mapped to the bundled Video/Player folder; .example never resolves on
    // a real network (RFC 2606), so nothing outside the app can answer it.
    public const string PlayerHost = "netra-player.example";
    public static readonly Uri PlayerPage = new($"https://{PlayerHost}/player.html");

    private static readonly string[] EmbedHosts = { "www.youtube-nocookie.com", "www.youtube.com" };

    public static bool IsAllowedTopLevel(string? uri) =>
        Uri.TryCreate(uri, UriKind.Absolute, out var parsed)
        && parsed.Scheme == Uri.UriSchemeHttps
        && string.Equals(parsed.Host, PlayerHost, StringComparison.OrdinalIgnoreCase)
        && parsed.AbsolutePath == PlayerPage.AbsolutePath
        && string.IsNullOrEmpty(parsed.Query)
        && string.IsNullOrEmpty(parsed.Fragment);

    // The IFrame API creates one embed frame; it may briefly be about:blank.
    public static bool IsAllowedFrame(string? uri)
    {
        if (uri is "about:blank" or "about:srcdoc")
        {
            return true;
        }

        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed) || parsed.Scheme != Uri.UriSchemeHttps)
        {
            return false;
        }

        var segments = parsed.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return Array.Exists(EmbedHosts, h => string.Equals(h, parsed.Host, StringComparison.OrdinalIgnoreCase))
            && segments.Length == 2
            && segments[0] == "embed"
            && YouTubeVideoId.IsValid(segments[1]);
    }

    // Messages are trusted only from Netra's own page, never from the embed.
    public static bool IsTrustedMessageSource(string? source) => IsAllowedTopLevel(source);
}
