namespace Netra.Desktop.ViewModels;

// Composes the four accessible views the AgentSpec/M5 guide require
// (Library, Study, Conversation, Preferences/status). MainWindow hosts this
// as its DataContext and binds each TabItem's content to the corresponding
// child view model — see Views/MainWindow.xaml.
public sealed class ShellViewModel : ViewModelBase, IDisposable
{
    public ShellViewModel(
        LibraryViewModel libraryViewModel,
        StudyViewModel studyViewModel,
        ConversationViewModel conversationViewModel,
        PreferencesViewModel preferencesViewModel,
        LectureViewModel? lectureViewModel = null)
    {
        Library = libraryViewModel;
        Study = studyViewModel;
        Conversation = conversationViewModel;
        Preferences = preferencesViewModel;
        Lecture = lectureViewModel;
        Library.LecturePlayRequested += OnLecturePlayRequested;
    }

    public LibraryViewModel Library { get; }
    public StudyViewModel Study { get; }
    public ConversationViewModel Conversation { get; }
    public PreferencesViewModel Preferences { get; }
    public LectureViewModel? Lecture { get; }

    // The window moves to the Lecture tab; the lecture opens there.
    public event EventHandler? ShowLectureRequested;

    private void OnLecturePlayRequested(object? sender, Library.VideoDiscoveryResult result)
    {
        if (Lecture is null)
        {
            return;
        }

        ShowLectureRequested?.Invoke(this, EventArgs.Empty);
        _ = Lecture.OpenResultAsync(result, CancellationToken.None);
    }

    public void Dispose()
    {
        Library.LecturePlayRequested -= OnLecturePlayRequested;
        Conversation.Dispose();
        Lecture?.Dispose();
    }
}
