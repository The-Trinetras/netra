namespace Netra.Desktop.Accessibility;

public sealed record Shortcut(string Keys, string Action)
{
    // What the screen reader reads for one row of the list.
    public override string ToString() => $"{Keys}: {Action}";
}

// Every keyboard shortcut in words, for the list in Preferences and status
// (F1). Key names are spelled out ("Control+Shift+P") because NVDA reads
// them more clearly than symbols. KeyboardCommands holds the gestures; a
// test keeps this list and those gestures in step.
public static class ShortcutGuide
{
    public const string PushToTalk = "F9";

    public static IReadOnlyList<Shortcut> Everywhere { get; } = new[]
    {
        new Shortcut("Hold F9", "Speak a question (push to talk). Pauses Netra's speech and any lecture first."),
        new Shortcut("Escape", "Stop Netra speaking at once."),
        new Shortcut("Control+P", "Pause Netra's speech."),
        new Shortcut("Control+Shift+P", "Continue after a pause."),
        new Shortcut("Control+Right arrow", "Next."),
        new Shortcut("Control+Left arrow", "Previous."),
        new Shortcut("Control+R", "Repeat."),
        new Shortcut("Control+L", "Where am I."),
        new Shortcut("Control+B", "Back to reading."),
        new Shortcut("Control+U", "Undo the last jump."),
        new Shortcut("Control+Q", "Return to the waiting question."),
        new Shortcut("Control+1 to Control+5", "Library, Study, Lecture, Conversation, Preferences and status."),
        new Shortcut("F1", "This list of shortcuts."),
    };

    public static IReadOnlyList<Shortcut> LectureTab { get; } = new[]
    {
        new Shortcut("K", "Play or pause the lecture."),
        new Shortcut("J", "Back 10 seconds."),
        new Shortcut("L", "Forward 10 seconds."),
        new Shortcut("T", "Where am I in the video."),
        new Shortcut("C", "Continue from where the video paused for your question."),
        new Shortcut("D", "Pause and describe this moment."),
    };
}
