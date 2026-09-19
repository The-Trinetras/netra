using System.Text.RegularExpressions;

namespace Netra.Desktop.Video;

// A YouTube video id: exactly 11 of [A-Za-z0-9_-]. Nothing else ever reaches
// the player page, so a pasted string can never become a URL or script.
public sealed partial record YouTubeVideoId
{
    private YouTubeVideoId(string value) => Value = value;

    public string Value { get; }

    public static bool IsValid(string? value) => value is not null && IdPattern().IsMatch(value);

    public static YouTubeVideoId Parse(string value) =>
        IsValid(value) ? new YouTubeVideoId(value) : throw new FormatException("Not a YouTube video id.");

    // Accepts a bare id or a YouTube link a teacher might share:
    // youtube.com/watch?v=, youtu.be/, youtube.com/embed|shorts|live/,
    // with or without www. or m. Anything else (other sites, playlists
    // without a video, text) is refused rather than guessed.
    public static bool TryParseLink(string? text, out YouTubeVideoId? id)
    {
        id = null;
        var trimmed = text?.Trim();
        if (string.IsNullOrEmpty(trimmed))
        {
            return false;
        }

        if (IsValid(trimmed))
        {
            id = new YouTubeVideoId(trimmed);
            return true;
        }

        if (!trimmed.Contains("://", StringComparison.Ordinal))
        {
            trimmed = "https://" + trimmed;
        }

        if (!Uri.TryCreate(trimmed, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttps && uri.Scheme != Uri.UriSchemeHttp))
        {
            return false;
        }

        var host = uri.Host.ToLowerInvariant();
        string? candidate = null;
        if (host == "youtu.be")
        {
            candidate = uri.AbsolutePath.Trim('/');
        }
        else if (host is "youtube.com" or "www.youtube.com" or "m.youtube.com" or "www.youtube-nocookie.com")
        {
            var segments = uri.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
            if (segments.Length == 1 && segments[0] == "watch")
            {
                candidate = QueryValue(uri.Query, "v");
            }
            else if (segments.Length == 2 && segments[0] is "embed" or "shorts" or "live")
            {
                candidate = segments[1];
            }
        }

        if (!IsValid(candidate))
        {
            return false;
        }

        id = new YouTubeVideoId(candidate!);
        return true;
    }

    public override string ToString() => Value;

    private static string? QueryValue(string query, string name)
    {
        foreach (var pair in query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = pair.Split('=', 2);
            if (parts.Length == 2 && parts[0] == name)
            {
                return Uri.UnescapeDataString(parts[1]);
            }
        }

        return null;
    }

    [GeneratedRegex("^[A-Za-z0-9_-]{11}$")]
    private static partial Regex IdPattern();
}
