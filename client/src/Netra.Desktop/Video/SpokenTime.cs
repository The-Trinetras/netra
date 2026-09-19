namespace Netra.Desktop.Video;

// Player times in words for the screen reader ("12 minutes 34 seconds"):
// NVDA reads "12:34" as a clock time or a ratio. Display text stays compact.
public static class SpokenTime
{
    public static string Words(long milliseconds)
    {
        var total = Math.Max(0, milliseconds) / 1000;
        var hours = total / 3600;
        var minutes = total % 3600 / 60;
        var seconds = total % 60;
        var parts = new List<string>(3);
        if (hours > 0)
        {
            parts.Add(Unit(hours, "hour"));
        }

        if (minutes > 0)
        {
            parts.Add(Unit(minutes, "minute"));
        }

        if (seconds > 0 || parts.Count == 0)
        {
            parts.Add(Unit(seconds, "second"));
        }

        return string.Join(" ", parts);
    }

    public static string Clock(long milliseconds)
    {
        var time = TimeSpan.FromMilliseconds(Math.Max(0, milliseconds));
        return time.TotalHours >= 1 ? time.ToString(@"h\:mm\:ss") : time.ToString(@"m\:ss");
    }

    private static string Unit(long value, string name) => value == 1 ? $"1 {name}" : $"{value} {name}s";
}
